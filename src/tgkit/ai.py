"""What every AI of the Telegram Assistant window shares: Claude Code (claude.py)
and the Gemini CLI (gemini.py).

Both runners take the same question, run their CLI headless in an empty work
folder with TGKIT_READONLY=1, and yield the same events:

    session   {"id"}                      the CLI's session id (kept per conversation)
    model     {"model"}
    token     {"text"}                    streamed words
    step      {"id", "command"}           a tgkit command it runs
    step_done {"id", "ok", "note"}
    tool_json {"command", "data"}         the JSON a --json command printed (becomes cards)
    usage     {"usage"}                   tokens of this answer (usage.KINDS)
    final     {"text", "session"}
    error     {"reason", "message", "detail"}

The rules below are written once, so the two CLIs allow the same tgkit read
commands and refuse the same changing ones. Whatever a CLI's own rules let
through, the tgkit it runs is read-only: cli.main refuses every changing
command when TGKIT_READONLY is set.
"""

import os
import re
import sys
from pathlib import Path

from . import config

PROVIDERS = ("claude", "gemini")
CHOICES = ("auto", "claude", "gemini", "off")
NAMES = {"claude": "Claude", "gemini": "Gemini"}
EVENTS = ("session", "model", "token", "step", "step_done", "tool_json", "usage", "final", "error")

# Every tasks form is allowed ("tgkit tasks --chat x --since 7d --json"), and a
# changing word is denied wherever it stands: first ("tasks done 1"), between
# options ("tasks --json done 1", which argparse runs as "done") or last.
TASK_WRITES = ("done", "undo", "reopen", "drop", "note", "add")  # the same as cli.TASK_WRITES
READ_COMMANDS = ["tgkit search *", "tgkit summary *", "tgkit tasks", "tgkit tasks *",
                 "tgkit chats*", "tgkit show *", "tgkit media *", "tgkit guide",
                 "tgkit today", "tgkit today *",
                 "tgkit usage", "tgkit usage *", "tgkit claude-status", "tgkit claude-status *",
                 "tgkit ai-status", "tgkit ai-status *"]
# export writes a file and logout ends the Telegram login: the user does them, never a session.
# import (import-crawl) writes chats: the user does it in Settings.
WRITE_COMMANDS = ("setup", "add", "sync", "logout", "export", "login", "import")
# A changing word also counts in quotes ("done" or 'done'): the shell drops them.
DENIED_COMMANDS = ([f"tgkit {w}*" for w in WRITE_COMMANDS]
                   + [p for w in TASK_WRITES for q in ("", '"', "'")
                      for p in (f"tgkit tasks {q}{w}{q}*", f"tgkit tasks * {q}{w}{q}", f"tgkit tasks * {q}{w}{q} *")])
# The same read commands as plain prefixes (a CLI that matches "starts with this word").
READ_PREFIXES = sorted({c.rstrip("*").strip() for c in READ_COMMANDS} | {"tgkit chats-log"})
READONLY_ENV = "TGKIT_READONLY"  # the same name as cli.READONLY_ENV

# The app's session reads the same playbook as every other session (playbook.md,
# also `tgkit guide` and the plugin skill), plus these rules of the app.
_APP_RULES = """
## In the Telegram Assistant app (this session)

You answer inside the Telegram Assistant window. Here these rules come before the ones above:

- Your only tools are tgkit's read commands, given to you as {tool}: search, summary, tasks,
  today, chats, show, media, guide, chats_log, usage, ai_status, claude_status. Each one is
  the tgkit command of the same name: put its options in the tool's fields (for
  `tgkit search "x" --chat Alpha --since 7d --json` call search with query "x", chat "Alpha",
  since "7d"). You have no shell and no file tools. Never read, list or print files.
- This session is read-only. Never try sync, setup, add, logout, export, import-crawl,
  `search --online`, or a task change (done, undo, reopen, drop, note, add), even when the user
  asks: tell them to use the window's buttons (Get new messages, Mark done, Save as file, Settings).
  The tools cannot do them anyway. A pattern search (`tgkit search --regex`) is search with
  regex true.
- If a chat has "stale": true, say its copy may be old and suggest the Get new messages button.
- "What do I need to do today" (or "hôm nay tôi cần làm gì"): run `tgkit today --json` and
  answer with a short list, most urgent first (due today, then people waiting for an answer,
  then the rest), each with who and which chat. Add nothing it does not hold.
- Answer short and plain; never show commands.
"""


def app_rules(tool: str) -> str:
    """The app's rules for one CLI; `tool` names how that CLI runs a command."""
    return _APP_RULES.format(tool=tool)


# Shell text a word may hold without quotes, the same in PowerShell and bash: letters
# (any language), digits and _ . , : / = + -. Nothing else is left to a shell to read.
_PLAIN = re.compile(r"[\w.,:/=+-]+")
# PowerShell also reads the curly quotes (U+2018..U+201F) as quotes: never allowed.
_CURLY = {chr(c) for c in range(0x2018, 0x2020)}
# Inside "...": PowerShell expands $ and `, bash also $ ` and \; a " ends it.
_DQ_BAD = set('$`\\"') | _CURLY
# Inside '...': nothing is expanded in either shell; a ' ends it.
_SQ_BAD = {"'"} | _CURLY


def shell_words(command: str) -> list[str] | None:
    """The words of a command when every shell reads it the same way, else None.
    Allow-list, not a deny list: a word is plain (_PLAIN), or one quoted string
    ('...' with no ' inside; "..." with no $ ` \\ " inside), or --option= and one
    quoted string. Words are split by single spaces only."""
    raw = str(command or "")
    if not raw.strip() or any(ord(c) < 32 or c in _CURLY or c == "\x7f" for c in raw):
        return None
    words, i, n = [], 0, len(raw)
    while i < n:
        if raw[i] == " ":
            i += 1
            continue
        head = ""
        m = re.compile(r"--[A-Za-z][A-Za-z-]*=(?=['\"])").match(raw, i)
        if m:
            head, i = m.group(0), m.end()
        if raw[i] in "'\"":
            q = raw[i]
            end = raw.find(q, i + 1)
            body = raw[i + 1:end] if end > 0 else None
            if body is None or set(body) & (_SQ_BAD if q == "'" else _DQ_BAD):
                return None
            i = end + 1
        else:
            m = _PLAIN.match(raw, i)
            if not m:
                return None  # a shell character outside quotes
            body, i = m.group(0), m.end()
        if i < n and raw[i] != " ":
            return None  # a quote glued to more text, or a shell character after a word
        words.append(head + body)
    return words


def allowed_command(command: str) -> bool:
    """A tgkit read command written so that no shell can read more into it (shell_words),
    starting with a read command, with no changing word and no --online. The window gives
    the AI no shell at all (mcp.py); this is the check of any shell text an AI CLI shows."""
    words = shell_words(command)
    if not words or len(words) < 2 or words[0] != "tgkit":
        return False
    cmd = " ".join(words[:2])
    if not any(cmd == p for p in READ_PREFIXES):
        return False
    low = [w.lower() for w in words]
    if low[1] == "tasks" and any(w in TASK_WRITES for w in low[2:]):
        return False
    if low[1] == "search" and any(w in ("--online", "--server") or w.startswith(("--online=", "--server="))
                                  for w in low[2:]):
        return False
    return low[1] not in WRITE_COMMANDS


_DASHES = {0x2014: "-", 0x2013: "-"}  # em dash, en dash (code points: no dash in the source)


def plain(text: str) -> str:
    """The model sometimes writes em or en dashes although the guide asks it not to;
    the app shows a plain hyphen instead (same mapping for the streamed words and
    the final answer, so they match)."""
    return (text or "").translate(_DASHES)


def tgkit_dirs() -> list[str]:
    """Folders holding the tgkit command of THIS install, so the AI runs the same one."""
    exe = Path(sys.executable).resolve().parent
    dirs = [exe, exe / "Scripts", exe.parent / "bin"]
    return [str(d) for d in dirs if (d / "tgkit").exists() or (d / "tgkit.exe").exists()]


def run_env(home: Path) -> dict:
    """The environment of a headless AI run: this data folder, read-only tgkit, UTF-8,
    and this install's tgkit first on PATH."""
    env = dict(os.environ)
    env["TGKIT_HOME"] = str(home)
    env[READONLY_ENV] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    extra = tgkit_dirs()
    if extra:
        env["PATH"] = os.pathsep.join(extra + [env.get("PATH", "")])
    return env


def choice(home: Path | None) -> str:
    """The user's pick in Settings: auto (the default), claude, gemini or off."""
    cfg = config.load(Path(home)) if home and config.config_path(Path(home)).exists() else {}
    value = str(cfg.get("ai_provider") or "auto")
    return value if value in CHOICES else "auto"


def pick(chosen: str, claude_ok: bool, claude_state: str | None,
         gemini_ok: bool, gemini_state: str | None) -> str | None:
    """Which AI answers: the one chosen, or in auto Claude first, then Gemini when it
    is signed in (also when Claude is installed but not logged in). None = offline."""
    if chosen == "off":
        return None
    if chosen == "claude":
        return "claude" if claude_ok else None
    if chosen == "gemini":
        return "gemini" if gemini_ok else None
    gemini_ready = gemini_ok and gemini_state == "connected"
    if claude_ok and not (claude_state == "not_logged_in" and gemini_ready):
        return "claude"
    return "gemini" if gemini_ready else None


def status(home: Path | None = None) -> dict:
    """`tgkit ai-status`: the choice, the AI that answers, and both CLIs' status."""
    from . import claude, gemini  # noqa: PLC0415

    st_claude = claude.status(home=home)
    st_gemini = gemini.status(home=home)
    chosen = choice(home)
    active = pick(chosen, st_claude["installed"], st_claude["state"],
                  st_gemini["installed"], st_gemini["state"])
    return {"choice": chosen, "active": active or "off", "claude": st_claude, "gemini": st_gemini}
