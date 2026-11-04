"""Claude mode for the app: a headless Claude Code session that answers with tgkit.

The app runs `claude -p --output-format stream-json` with the question on
stdin. The session has no shell and no built-in tools (`--tools ""`; Bash and
PowerShell are denied as well): its only tools are tgkit's read commands, served
by Telegram Assistant's own MCP server (mcp.py, passed per run with
`--mcp-config` and `--strict-mcp-config`), which runs tgkit with an argv list from
typed fields, so no text from the model reaches a shell. Anything else is denied
without a prompt, and the tgkit it runs is read-only (TGKIT_READONLY): it refuses
to change a task, sync, add chats or set up. Its words stream into the chat; each tgkit command it runs
shows as a step, and the JSON it got back becomes the same cards the offline
mode draws. The session id is kept per conversation, so a follow-up resumes it.

If Claude is missing, not signed in, or fails, the caller answers offline.
"""

import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Iterator

from . import ai, config, mcp, playbook, usage
from .ai import DENIED_COMMANDS, READ_COMMANDS, READONLY_ENV, TASK_WRITES, plain  # noqa: F401  (shared, ai.py)
from .store import atomic_write

# Claude Code's shells (Bash, or PowerShell on a PC set up that way) are never
# offered: a wildcard rule like PowerShell(tgkit search *) also matches
# "tgkit search ([IO.File]::WriteAllText(...))". The only allowed tools are the
# tgkit server's (mcp.py), the same tools Gemini gets. Deny rules win over allow
# rules. The session also gets TGKIT_READONLY=1.
SHELLS = ("Bash", "PowerShell")
ALLOWED = [f"mcp__{mcp.SERVER}__{t}" for t in mcp.TOOLS]
DENIED = ["Write", "Edit", "NotebookEdit", "WebFetch", "WebSearch", "Read", "Glob", "Grep", "Task", *SHELLS]
# Claude in Chrome (`--chrome`, Claude Code 2.1.317; tool names from its binary), only when
# the user turned the browser on (connectors.py). Reading only: `computer` clicks, types and
# presses keys in one tool and browser_batch runs several, so both are denied with every
# tool that fills, runs scripts, uploads, records or changes the browser. Any other
# claude-in-chrome tool is in neither list and so not allowed (dontAsk refuses it).
# The tools that open a page (navigate; tabs_create_mcp, which takes no address in 2.1.318)
# are in neither list either: a permission rule cannot look at an address, so each call
# goes to tgkit's PreToolUse hook (connectors.chrome_hook, passed per run with --settings),
# which allows it only for a page on the user's list of sites. If the hook does not run,
# dontAsk refuses the call, and a hook that cannot run here at all turns Claude's browser
# off (hook_problem). Claude cannot click at all (computer is denied).
CHROME = "mcp__claude-in-chrome__"
CHROME_ALLOWED = [CHROME + t for t in ("read_page", "get_page_text", "find", "tabs_context_mcp")]
CHROME_HOOKED = [CHROME + t for t in ("navigate", "tabs_create_mcp")]
CHROME_DENIED = [CHROME + t for t in ("computer", "browser_batch", "form_input", "javascript_tool", "file_upload",
                                      "upload_image", "gif_creator", "shortcuts_execute", "shortcuts_list",
                                      "switch_browser", "resize_window", "read_console_messages",
                                      "read_network_requests", "tabs_close_mcp", "list_connected_browsers",
                                      "select_browser")]
HOOK_SCRIPT = """import sys
sys.path.insert(0, {root!r})
from tgkit import connectors
sys.exit(connectors.chrome_hook({home!r}))
"""

# The app's session reads the same playbook as every other Claude Code session
# (playbook.md, also `tgkit guide` and the plugin skill), plus the rules of the app.
APP_RULES = ai.app_rules("the tools of the tgkit server (mcp__tgkit__search and the others)")
GUIDE = playbook.text() + APP_RULES

FALLBACK_HINTS = (
    ("unknown option", "compat"),
    ("not logged in", "login"), ("please run /login", "login"), ("invalid api key", "login"),
    ("authentication", "login"), ("oauth", "login"),
)


def find_claude() -> list[str] | None:
    """The command that starts Claude Code, or None. TGKIT_CLAUDE overrides it (tests)."""
    override = os.environ.get("TGKIT_CLAUDE")
    if override:
        if override.lower() in ("0", "off", "none"):
            return None
        return [sys.executable, override] if override.endswith(".py") else [override]
    found = shutil.which("claude")
    return [found] if found else None


NO_WINDOW = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
INSTALL_URL = "https://code.claude.com/docs/en/setup"
# The model aliases every Claude Code knows; more are read from `claude --help`.
BASE_MODELS = ("sonnet", "opus", "haiku")
_HELP_CACHE: dict = {}


def _run(cmd: list[str], *args: str, timeout: int = 20) -> tuple[int, str]:
    """Run a quick claude command (help, version, auth status). Never sends a prompt."""
    try:
        done = subprocess.run([*cmd, *args], stdin=subprocess.DEVNULL, capture_output=True,
                              timeout=timeout, creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return -1, ""
    out = (done.stdout or b"").decode("utf-8", "replace") + (done.stderr or b"").decode("utf-8", "replace")
    return done.returncode, out


def _option_block(text: str, flag: str) -> str:
    """The help lines of one option: its line and the indented lines under it."""
    m = re.search(rf"^\s*{re.escape(flag)}\b.*(?:\n(?!\s*-)\s{{6,}}\S.*)*", text, re.M)
    return m.group(0) if m else ""


def capabilities(cmd: list[str] | None) -> dict:
    """What this Claude Code accepts, read from its own `--help` (cached per command).
    Nothing is guessed: an option missing from the help is not offered."""
    if not cmd:
        return {"model": False, "models": [], "effort": False, "efforts": [], "auth": False}
    key = tuple(cmd)
    if key in _HELP_CACHE:
        return _HELP_CACHE[key]
    code, text = _run(cmd, "--help")
    model_help = _option_block(text, "--model")
    effort_help = _option_block(text, "--effort")
    efforts = []
    m = re.search(r"\(([a-z]+(?:,\s*[a-z]+)+)\)", effort_help)
    if m:
        efforts = [e.strip() for e in m.group(1).split(",") if e.strip()]
    found = re.findall(r"'([a-z][a-z0-9-]*)'", model_help)
    models = list(dict.fromkeys([*BASE_MODELS, *found])) if model_help else []
    caps = {"model": bool(model_help), "models": models, "effort": bool(effort_help and efforts),
            "efforts": efforts, "auth": bool(re.search(r"^\s+auth\b", text, re.M)),
            "chrome": bool(_option_block(text, "--chrome")) and bool(_option_block(text, "--no-chrome"))}
    if code == 0:
        _HELP_CACHE[key] = caps
    return caps


def _caps_view(caps: dict) -> dict:
    """Capabilities as status shows them; "model" and "effort" there are the chosen values."""
    return {"models": caps["models"], "efforts": caps["efforts"], "model_supported": caps["model"],
            "effort_supported": caps["effort"], "auth_supported": caps["auth"]}


def settings(home: Path | None) -> dict:
    cfg = config.load(Path(home)) if home and config.config_path(Path(home)).exists() else {}
    return {"model": str(cfg.get("claude_model") or ""), "effort": str(cfg.get("claude_effort") or "")}


def status(cmd: list[str] | None = None, home: Path | None = None) -> dict:
    """Claude Code on this computer, in plain states:
    missing, not_logged_in, connected, or unknown (installed, login not checkable)."""
    cmd = cmd if cmd is not None else find_claude()
    chosen = settings(home)
    out = {"state": "missing", "installed": False, "version": None, "path": None, "logged_in": None,
           "account": None, "plan": None, "auth_method": None, "install_url": INSTALL_URL,
           "model": chosen["model"], "effort": chosen["effort"]}
    if not cmd:
        out.update(_caps_view(capabilities(None)))
        out["fix"] = ["Install Claude Code (free with a Claude plan): " + INSTALL_URL,
                      "Then open a terminal, type claude and log in once.",
                      "Come back and press Check again."]
        return out
    out["installed"], out["path"] = True, cmd[-1]
    code, text = _run(cmd, "--version")
    m = re.search(r"\d+\.\d+\.\d+", text)
    out["version"] = m.group(0) if m else None
    caps = capabilities(cmd)
    out.update(_caps_view(caps))
    if code == -1 and not out["version"]:
        out["state"] = "missing"
        out["fix"] = ["Claude Code is installed but does not start. Reinstall it: " + INSTALL_URL]
        return out
    out["state"] = "unknown"
    if caps["auth"]:
        code, text = _run(cmd, "auth", "status")
        info = None
        start = text.find("{")
        if start >= 0:
            try:
                info = json.loads(text[start:text.rfind("}") + 1])
            except ValueError:
                info = None
        if isinstance(info, dict) and "loggedIn" in info:
            out["logged_in"] = bool(info.get("loggedIn"))
            out["state"] = "connected" if out["logged_in"] else "not_logged_in"
            out["account"] = info.get("email") or info.get("orgName")
            out["plan"] = info.get("subscriptionType")
            out["auth_method"] = info.get("authMethod")
    out["fix"] = {
        "connected": [],
        "not_logged_in": ["Open a terminal window and type: claude auth login",
                          "Log in with your Claude account in the browser page that opens.",
                          "Come back and press Check again."],
        "unknown": ["If answers fall back to Telegram Assistant only, open a terminal, type claude and log in."],
    }[out["state"]]
    return out


_tgkit_dirs = ai.tgkit_dirs  # the folders of THIS install's tgkit, so Claude runs the same one


def mcp_file(home: Path) -> Path:
    """The per-run MCP config (`--mcp-config`): only the tgkit server."""
    return Path(home) / "app" / "claude" / "tgkit-mcp.json"


def mcp_config(home: Path) -> dict:
    return {"mcpServers": {mcp.SERVER: {"type": "stdio", **mcp.server_command(home)}}}


def hooks_file(home: Path) -> Path:
    """The per-run settings (`--settings`) holding only the Claude in Chrome address hook."""
    return Path(home) / "app" / "claude" / "chrome-hooks.json"


def hook_entry(home: Path) -> dict:
    """The hook as Claude Code runs it: the program in "command", its arguments in "args"
    (Claude Code 2.1.318 then starts it directly, with no shell). A command line would go
    through Git Bash or, without Git Bash, PowerShell, which does not run a quoted first
    word, and every default install has a space ("Telegram Assistant/.venv"). onFailure
    block asks Claude Code to refuse the call when the hook fails to start; it is not
    measured on 2.1.318, so the run also uses dontAsk, where a call no hook allowed is
    refused anyway. -I: no PYTHONPATH or user site."""
    script = Path(home) / "app" / "claude" / "chrome-hook.py"
    return {"type": "command", "command": mcp.python_exe(), "args": ["-I", str(script)],
            "onFailure": "block", "timeout": 60}


def write_chrome_hook(home: Path) -> Path:
    """Write the hook script and the settings file that runs it before navigate and
    tabs_create_mcp; the script imports THIS tgkit and reads this data folder."""
    work = Path(home) / "app" / "claude"
    work.mkdir(parents=True, exist_ok=True)
    script = work / "chrome-hook.py"
    root = str(Path(__file__).resolve().parent.parent)
    atomic_write(script, HOOK_SCRIPT.format(root=root, home=str(Path(home))))
    conf = {"hooks": {"PreToolUse": [{"matcher": "|".join(CHROME_HOOKED), "hooks": [hook_entry(home)]}]}}
    atomic_write(hooks_file(home), json.dumps(conf, indent=2))
    return hooks_file(home)


def hook_problem(home: Path) -> str | None:
    """Does the address hook run on this computer? It is started the way Claude Code starts
    it (the program and its arguments, Claude's environment) with a call it must refuse in
    its own words. None when it does; else what went wrong, and Claude then gets no browser
    (fail closed) and Settings says so. Only a pass is kept (per hook and version): a
    failure is checked again next time, so a fixed hook brings the browser back."""
    from . import __version__, connectors  # noqa: PLC0415

    write_chrome_hook(home)
    entry = hook_entry(home)
    script = Path(entry["args"][-1])
    key = json.dumps([entry, script.read_text(encoding="utf-8"), __version__])
    seen = Path(home) / "app" / "claude" / "chrome-hook-check.json"
    try:
        cached = json.loads(seen.read_text(encoding="utf-8"))
        if isinstance(cached, dict) and cached.get("key") == key and cached.get("problem") is None:
            return None
    except (OSError, ValueError):
        pass
    probe = {"hook_event_name": "PreToolUse", "tool_name": "tgkit_self_check", "tool_input": {}}
    try:
        done = subprocess.run([entry["command"], *entry["args"]], input=json.dumps(probe).encode("utf-8"),
                              capture_output=True, timeout=entry["timeout"], env=ai.run_env(Path(home)),
                              creationflags=NO_WINDOW)
        err = done.stderr.decode("utf-8", "replace").strip()
        ok = done.returncode == 2 and connectors.HOOK_ONLY in err
        problem = None if ok else f"exit code {done.returncode}: {err[-300:]}"
    except (OSError, subprocess.TimeoutExpired) as exc:
        problem = f"{type(exc).__name__}: {exc}"[:300]
    if problem is None:
        atomic_write(seen, json.dumps({"key": key, "problem": None}))
    else:
        with contextlib.suppress(OSError):
            seen.unlink()  # an older pass for another key: never kept beside a failure
    return problem


class ClaudeRunner:
    name = "claude"

    def __init__(self, cmd: list[str] | None = None, model: str | None = None):
        self.cmd = cmd if cmd is not None else find_claude()
        self.model = model or os.environ.get("TGKIT_CLAUDE_MODEL") or None
        self.compat = False  # an older claude without the newer flags

    @property
    def available(self) -> bool:
        return bool(self.cmd)

    def chrome(self, home: Path) -> bool:
        """Claude in Chrome for this answer: the user turned the browser on, this Claude
        Code has --chrome, and the address hook runs here (else no browser: fail closed)."""
        from . import connectors  # noqa: PLC0415

        return (connectors.browser_on(home) and bool(capabilities(self.cmd).get("chrome"))
                and hook_problem(home) is None)

    # -------------------------------------------------------------- command

    def args(self, home: Path, session: str | None, guide_file: Path | None) -> list[str]:
        a = list(self.cmd) + ["-p", "--output-format", "stream-json", "--verbose"]
        if not self.compat:
            # No --safe-mode: it also turns off the --mcp-config servers, so Claude had no
            # tgkit tools at all. The user's settings files are skipped instead.
            a += ["--include-partial-messages", "--tools", "", "--permission-mode", "dontAsk",
                  "--setting-sources", ""]
            if guide_file is not None:
                a += ["--append-system-prompt-file", str(guide_file)]
        a += ["--mcp-config", str(mcp_file(home)), "--strict-mcp-config"]
        chrome = self.chrome(home)
        if capabilities(self.cmd).get("chrome"):
            a += ["--chrome" if chrome else "--no-chrome"]
        if chrome and not self.compat:  # the address hook (navigate, tabs_create_mcp); no hook: refused
            a += ["--settings", str(hooks_file(home))]
        a += ["--allowedTools", *ALLOWED, *(CHROME_ALLOWED if chrome else []),
              "--disallowedTools", *DENIED, *CHROME_DENIED]
        chosen = settings(home)
        model = self.model or chosen["model"]
        if model:
            a += ["--model", model]
        if chosen["effort"]:
            caps = capabilities(self.cmd)
            if caps["effort"] and chosen["effort"] in caps["efforts"]:
                a += ["--effort", chosen["effort"]]
        if session:
            a += ["--resume", session]
        return a

    def _prompt(self, text: str, session: str | None, chats: list[str]) -> str:
        if session and not self.compat:
            return text
        today = datetime.now().strftime("%Y-%m-%d")
        head = "" if not self.compat else GUIDE + "\n"
        names = ", ".join(chats) if chats else "(none yet)"
        return f"{head}Today is {today}. The user's chats: {names}.\n\nUser question:\n{text}"

    # -------------------------------------------------------------- run

    def stream(self, text: str, home: Path, session: str | None = None,
               chats: list[str] | None = None) -> Iterator[dict]:
        """Events: session, model, token, step, step_done, tool_json, usage, final, error."""
        first = None
        for ev in self._stream_once(text, home, session, chats or []):
            if ev["type"] == "error" and ev.get("reason") == "compat" and not self.compat:
                first = ev
                break
            yield ev
        if first is not None:
            self.compat = True
            yield from self._stream_once(text, home, session, chats or [])

    def _stream_once(self, text, home, session, chats) -> Iterator[dict]:
        home = Path(home)
        work = home / "app" / "claude"
        work.mkdir(parents=True, exist_ok=True)
        guide = work / "tgkit-guide.md"
        atomic_write(guide, GUIDE)
        atomic_write(mcp_file(home), json.dumps(mcp_config(home), indent=2))
        if self.chrome(home):
            write_chrome_hook(home)
        env = ai.run_env(home)
        flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
        try:
            proc = subprocess.Popen(
                self.args(home, session, guide), cwd=str(work), env=env,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                creationflags=flags)
        except OSError as exc:
            yield {"type": "error", "reason": "missing", "message": f"Claude could not start ({exc})."}
            return
        err_tail: deque = deque(maxlen=40)

        def drain():
            for raw in proc.stderr:
                err_tail.append(raw.decode("utf-8", "replace").rstrip())
        t = threading.Thread(target=drain, daemon=True)
        t.start()
        try:
            proc.stdin.write(self._prompt(text, session, chats).encode("utf-8"))
            proc.stdin.close()
        except OSError:
            pass

        tools: dict = {}
        streamed = False
        final = None
        new_session = None
        blocked = None
        try:
            for raw in proc.stdout:
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                kind = msg.get("type")
                if msg.get("session_id") and msg.get("session_id") != new_session:
                    new_session = msg["session_id"]
                    yield {"type": "session", "id": new_session}
                if kind == "system" and msg.get("subtype") == "init" and msg.get("model"):
                    yield {"type": "model", "model": str(msg["model"])}
                elif kind == "stream_event":
                    ev = msg.get("event") or {}
                    delta = ev.get("delta") or {}
                    if ev.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
                        streamed = True
                        yield {"type": "token", "text": plain(delta.get("text", ""))}
                elif kind == "assistant":
                    for block in (msg.get("message") or {}).get("content") or []:
                        if block.get("type") == "tool_use":
                            name = str(block.get("name") or "")
                            params = block.get("input") if isinstance(block.get("input"), dict) else {}
                            tool = mcp.tool_of(name)
                            cmd = mcp.describe(tool, params) if tool else str(params.get("command") or name)
                            if not tool and name in CHROME_ALLOWED + CHROME_HOOKED and self.chrome(home):
                                tool = name  # a reading tool of Claude in Chrome, allowed this answer
                                what = params.get("url") or params.get("query") or ""
                                cmd = f"browser: {name[len(CHROME):].replace('_', ' ')} {str(what)[:120]}".rstrip()
                            tools[block.get("id")] = (tool, name, cmd, params)
                            yield {"type": "step", "id": block.get("id"), "command": cmd}
                        elif block.get("type") == "text" and not streamed:
                            yield {"type": "token", "text": plain(block.get("text", ""))}
                elif kind == "user":
                    for block in (msg.get("message") or {}).get("content") or []:
                        if not isinstance(block, dict) or block.get("type") != "tool_result":
                            continue
                        body = block.get("content")
                        if isinstance(body, list):
                            body = "".join(b.get("text", "") for b in body if isinstance(b, dict))
                        body = body or ""
                        ok = not block.get("is_error")
                        yield {"type": "step_done", "id": block.get("tool_use_id"), "ok": ok,
                               "note": "" if ok else body[:300]}
                        tool, name, cmd, params = tools.get(block.get("tool_use_id")) or (None, "", "", {})
                        if ok and not tool:
                            # A tool outside the tgkit server ran (a shell, a file tool):
                            # Claude's rules should have refused it. Stop now.
                            blocked = f"{name}: {cmd}"
                            break
                        if name.startswith(CHROME):
                            from . import connectors  # noqa: PLC0415
                            if ok and name in CHROME_HOOKED:
                                # The hook should have refused an address off the list: if one
                                # was opened anyway, stop the answer (nothing more is read).
                                try:
                                    for k, v in params.items():
                                        if str(k).lower() in ("url", "href", "uri", "link") or \
                                                (isinstance(v, str) and "//" in v):
                                            connectors.url_check(v, connectors.sites(home))
                                except connectors.UserError:
                                    blocked = f"{name}: {cmd}"
                                    break
                            if ok:
                                connectors.record_status(home, "claude", "connected")
                            elif connectors.NOT_CONNECTED.search(body):
                                connectors.record_status(home, "claude", "not_connected")
                        if ok and "--json" in cmd:
                            try:
                                yield {"type": "tool_json", "command": cmd, "data": json.loads(body)}
                            except ValueError:
                                pass
                    if blocked:
                        break
                elif kind == "result":
                    final = msg
                    used = usage.from_result(msg)
                    if used:
                        yield {"type": "usage", "usage": used}
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait()
            t.join(timeout=2)

        if blocked:
            yield {"type": "error", "reason": "blocked", "detail": blocked[-400:],
                   "message": "Claude tried something Telegram Assistant does not allow, so it was stopped."}
            return
        if final and not final.get("is_error") and proc.returncode == 0:
            yield {"type": "final", "text": plain(final.get("result") or ""), "session": new_session}
            return
        detail = " ".join(err_tail) or (final or {}).get("result") or f"exit code {proc.returncode}"
        low = detail.lower()
        reason = next((r for k, r in FALLBACK_HINTS if k in low), "failed")
        message = {
            "login": "Claude is not signed in on this computer.",
            "compat": "This Claude Code is older than Telegram Assistant expects.",
        }.get(reason, "Claude could not answer this time.")
        yield {"type": "error", "reason": reason, "message": message, "detail": detail[-400:]}
