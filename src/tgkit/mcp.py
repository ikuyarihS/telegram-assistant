"""tgkit's read commands as tools for the AI of the window: a small MCP server on stdio.

Both AI CLIs (Claude Code and the Gemini CLI) get these tools and no shell. The
model fills typed JSON fields (a query, a chat name, a number); this server checks
them against the table below and runs `python -m tgkit <command> ...` with an
argv list, never through PowerShell, cmd or bash. So text from the model is only
ever data: "([IO.File]::WriteAllText(...))" or "$(rm a)" is searched for, not run.

- Values go as `--option=value`, positionals after `--`: a value that starts with
  "-" is never read as an option.
- No tool can change anything: no sync, setup, add, export, import or logout, no
  `search --online`, and `tasks` takes only its list forms. The tgkit it runs gets
  TGKIT_READONLY=1 as well, so it refuses every changing command anyway.
- Run: python -m tgkit.mcp --home <data folder>   (the app writes this into the
  CLI's per-run config; the user's own settings are never edited).
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

SERVER = "tgkit"  # no underscore: Gemini splits mcp_<server>_<tool> at the first one
PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
NO_WINDOW = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
TIMEOUT = 120
MAX_TEXT = 500
TASK_VIEWS = ("list", "open", "mine", "all")

# name: (tgkit command, adds --json, description, positionals, options)
# positional / option: name -> (kind, description); kind is "str", "int", "bool" or a tuple of choices.
TOOLS = {
    "search": ("search", True, "Find messages in the copied chats (tgkit search --json). Accents are optional.",
               {"query": ("str", "the words to find, or a pattern when regex is true")},
               {"chat": ("str", "only chats whose name contains this"),
                "regex": ("bool", "the query is a regular expression, e.g. a|b or (x); accents must match"),
                "since": ("str", "only messages since e.g. 7d, 2w, 2026-09-01"),
                "context": ("int", "also show N messages around each hit"),
                "limit": ("int", "show at most N (0 = all), default 30"),
                "case": ("bool", "match upper/lower case exactly")}),
    "summary": ("summary", True, "Who said what in one chat or all chats (tgkit summary --json).",
                {"chat": ("str", "a chat name or part of it; empty = all chats")},
                {"since": ("str", "period, default 7d")}),
    "tasks": ("tasks", True, "The task list (tgkit tasks --json). It only lists; it never changes a task.",
              {"view": (TASK_VIEWS, "list (default), open, mine or all")},
              {"chat": ("str", "only tasks from chats whose name contains this"),
               "since": ("str", "only tasks since e.g. 7d")}),
    "today": ("today", True, "What to do today, most urgent first (tgkit today --json).",
              {}, {"lang": (("en", "vi"), "en or vi")}),
    "chats": ("chats", False, "The chats Telegram Assistant follows, with their message counts (tgkit chats).",
              {}, {}),
    "show": ("show", False, "Read the messages of one chat (tgkit show).",
             {"chat": ("str", "a chat name or part of it")},
             {"last": ("int", "the newest N messages, default 40"), "all": ("bool", "every message"),
              "id": ("int", "one message by its id"), "since": ("str", "since e.g. 7d or 2026-09-01"),
              "until": ("str", "until a date"), "sender": ("str", "only this sender"),
              "thread": ("int", "only replies to this message id"), "width": ("int", "cut long lines at N")}),
    "media": ("media", False, "The files and photos of a chat (tgkit media).",
              {"chat": ("str", "a chat name or part of it")}, {}),
    "guide": ("guide", False, "How to use these tools well (tgkit guide).", {}, {}),
    "chats_log": ("chats-log", True, "Earlier conversations in this window (tgkit chats-log --json).",
                  {"id": ("str", "one conversation")},
                  {"last": ("int", "the newest N conversations (0 = all)"),
                   "cards": ("bool", "include the result cards")}),
    "usage": ("usage", True, "Tokens used by the AI answers (tgkit usage --json).",
              {}, {"conversation": ("str", "also the total of one conversation (its id)")}),
    "ai_status": ("ai-status", True, "Which AI answers and whether it is signed in (tgkit ai-status --json).",
                  {}, {}),
    "claude_status": ("claude-status", True, "Claude Code on this computer (tgkit claude-status --json).", {}, {}),
}
# Options of these tgkit commands no tool offers (printed by the tests, so none goes unnoticed).
NOT_OFFERED = {"search": ("online",), "tasks": ("rest", "due")}


class ToolError(ValueError):
    pass


def _schema(kind, text) -> dict:
    if isinstance(kind, tuple):
        return {"type": "string", "enum": list(kind), "description": text}
    return {"type": {"str": "string", "int": "integer", "bool": "boolean"}[kind], "description": text}


def tool_list() -> list[dict]:
    out = []
    for name, (_cmd, _js, text, pos, opts) in TOOLS.items():
        props = {k: _schema(*v) for k, v in {**pos, **opts}.items()}
        required = ["query"] if name == "search" else []
        out.append({"name": name, "description": text,
                    "inputSchema": {"type": "object", "properties": props, "required": required,
                                    "additionalProperties": False}})
    return out


def _value(tool: str, key: str, kind, value) -> str | bool:
    if isinstance(kind, tuple):
        if not isinstance(value, str) or value.lower() not in kind:
            if tool == "tasks" and isinstance(value, str):
                raise ToolError("Telegram Assistant is read-only here: tasks only lists "
                                f"({', '.join(TASK_VIEWS)}). Task changes use the window's buttons.")
            raise ToolError(f"{key} must be one of: {', '.join(kind)}.")
        return value.lower()
    if kind == "bool":
        if not isinstance(value, bool):
            raise ToolError(f"{key} must be true or false.")
        return value
    if kind == "int":
        if isinstance(value, bool) or not isinstance(value, int) or not -1_000_000 <= value <= 1_000_000:
            raise ToolError(f"{key} must be a whole number.")
        return str(value)
    if not isinstance(value, str):
        raise ToolError(f"{key} must be text.")
    if len(value) > MAX_TEXT or any(ord(c) < 32 and c not in "\t" for c in value):
        raise ToolError(f"{key} is too long or holds control characters.")
    return value


def argv(tool: str, args) -> list[str]:
    """The tgkit argv of one tool call (after `python -m tgkit`). Raises ToolError."""
    if tool not in TOOLS:
        raise ToolError(f"There is no tool named {tool!r}.")
    cmd, add_json, _text, pos, opts = TOOLS[tool]
    args = {} if args is None else args
    if not isinstance(args, dict):
        raise ToolError("The arguments must be an object.")
    unknown = sorted(set(args) - set(pos) - set(opts))
    if unknown:
        raise ToolError(f"Unknown field(s): {', '.join(map(str, unknown))}.")
    if tool == "search" and not isinstance(args.get("query"), str):
        raise ToolError("search needs a query.")
    out = [cmd]
    for key, (kind, _t) in opts.items():
        if args.get(key) is None:
            continue
        value = _value(tool, key, kind, args[key])
        if value is True:
            out.append(f"--{key}")
        elif value is not False:
            out.append(f"--{key}={value}")
    if add_json:
        out.append("--json")
    rest = [_value(tool, k, kind, args[k]) for k, (kind, _t) in pos.items() if args.get(k) not in (None, "")]
    if rest:
        out += ["--", *rest]
    return out


def describe(tool: str, args) -> str:
    """The step line the window shows, e.g. tgkit search "biên bản" --json (shown, never run)."""
    try:
        words = argv(tool, args)
    except ToolError:
        return f"tgkit {TOOLS[tool][0] if tool in TOOLS else tool}"
    shown = [w for w in words if w != "--"]
    plain = re.compile(r"[\w.,:/=+-]+")
    return "tgkit " + " ".join(w if plain.fullmatch(w) else json.dumps(w, ensure_ascii=False) for w in shown)


def tool_of(name: str) -> str | None:
    """The tool of a CLI's tool name: Gemini says mcp_tgkit_search, Claude mcp__tgkit__search."""
    name = str(name or "")
    for head in (f"mcp__{SERVER}__", f"mcp_{SERVER}_", f"{SERVER}__", f"{SERVER}_"):
        if name.startswith(head) and name[len(head):] in TOOLS:
            return name[len(head):]
    return None


def python_exe() -> str:
    """The python that runs the server and tgkit: python.exe rather than the window's pythonw.exe."""
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe" and (exe.parent / "python.exe").exists():
        return str(exe.parent / "python.exe")
    return str(exe)


def server_command(home: Path) -> dict:
    """The server as the CLIs' config describes it (command + args, no shell)."""
    return {"command": python_exe(), "args": ["-m", "tgkit.mcp", "--home", str(Path(home))]}


def run(tool: str, args, home: str | None) -> tuple[bool, str]:
    """Run one tool: (ok, text). Bad arguments are an error answer, not a crash."""
    try:
        words = argv(tool, args)
    except ToolError as exc:
        return False, str(exc)
    env = dict(os.environ)
    if home:
        env["TGKIT_HOME"] = home
    env["TGKIT_READONLY"] = "1"  # the same name as cli.READONLY_ENV
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        done = subprocess.run([python_exe(), "-m", "tgkit", *words], env=env, stdin=subprocess.DEVNULL,
                              capture_output=True, timeout=TIMEOUT, shell=False, creationflags=NO_WINDOW)
    except subprocess.TimeoutExpired:
        return False, f"tgkit {words[0]} took longer than {TIMEOUT} seconds."
    except OSError as exc:
        return False, f"tgkit could not start ({exc})."
    out = (done.stdout or b"").decode("utf-8", "replace")
    err = (done.stderr or b"").decode("utf-8", "replace")
    if done.returncode == 0:
        return True, out if out.strip() else (err or "(no output)")
    return False, (err + out).strip() or f"tgkit {words[0]} failed (exit code {done.returncode})."


def handle(msg: dict, home: str | None) -> dict | None:
    """One JSON-RPC message in, its reply out (None for a notification)."""
    method, mid = msg.get("method"), msg.get("id")
    if mid is None:
        return None
    params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
    if method == "initialize":
        asked = params.get("protocolVersion")
        from . import __version__  # noqa: PLC0415
        result = {"protocolVersion": asked if asked in PROTOCOLS else PROTOCOLS[0],
                  "capabilities": {"tools": {"listChanged": False}},
                  "serverInfo": {"name": SERVER, "version": __version__}}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": tool_list()}
    elif method == "tools/call":
        ok, text = run(str(params.get("name") or ""), params.get("arguments"), home)
        result = {"content": [{"type": "text", "text": text}], "isError": not ok}
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Unknown method {method}"}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def serve(home: str | None, stdin=None, stdout=None) -> None:
    stdin = stdin or sys.stdin.buffer
    stdout = stdout or sys.stdout.buffer
    for raw in stdin:
        line = raw.decode("utf-8", "replace").strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        else:
            reply = handle(msg, home) if isinstance(msg, dict) else None
        if reply is not None:
            stdout.write((json.dumps(reply, ensure_ascii=False) + "\n").encode("utf-8"))
            stdout.flush()


def main(argv_in=None) -> int:
    words = list(sys.argv[1:] if argv_in is None else argv_in)
    home = words[words.index("--home") + 1] if "--home" in words[:-1] else os.environ.get("TGKIT_HOME")
    serve(home)
    return 0


if __name__ == "__main__":
    sys.exit(main())
