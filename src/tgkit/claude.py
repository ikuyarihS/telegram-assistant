"""Claude mode for the app: a headless Claude Code session that answers with tgkit.

The app runs `claude -p --output-format stream-json` with the question on
stdin. The session may use only its shell tool (Bash, or PowerShell where Claude
Code runs commands in PowerShell, as on many Windows PCs), and only for tgkit's
read commands (search, summary, tasks lists, chats, show, media). Anything else
is denied without a prompt, and the tgkit it runs is read-only (TGKIT_READONLY):
it refuses to change a task, sync, add chats or set up, however the command is
written. Its words stream into the chat; each tgkit command it runs
shows as a step, and the JSON it got back becomes the same cards the offline
mode draws. The session id is kept per conversation, so a follow-up resumes it.

If Claude is missing, not signed in, or fails, the caller answers offline.
"""

import json
import os
import shutil
import subprocess
import sys
import threading
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Iterator

from . import playbook
from .store import atomic_write

# Claude Code's shell tool is Bash, or PowerShell on a PC set up that way. Every
# rule is written for both, so neither shell can run more than the other.
SHELLS = ("Bash", "PowerShell")
# Every tasks form is allowed ("tgkit tasks --chat x --since 7d --json"), and a
# changing word is denied wherever it stands: first ("tasks done 1"), between
# options ("tasks --json done 1", which argparse runs as "done") or last. Deny
# rules win over allow rules. The session also gets TGKIT_READONLY=1, so the
# tgkit CLI itself refuses every changing command however it is written.
TASK_WRITES = ("done", "undo", "reopen", "drop", "note", "add")  # the same as cli.TASK_WRITES
READ_COMMANDS = ["tgkit search *", "tgkit summary *", "tgkit tasks", "tgkit tasks *",
                 "tgkit chats*", "tgkit show *", "tgkit media *", "tgkit guide"]
DENIED_COMMANDS = (["tgkit setup*", "tgkit add*", "tgkit sync*"]
                   + [p for w in TASK_WRITES
                      for p in (f"tgkit tasks {w}*", f"tgkit tasks * {w}", f"tgkit tasks * {w} *")])
READONLY_ENV = "TGKIT_READONLY"  # the same name as cli.READONLY_ENV
ALLOWED = [f"{shell}({c})" for shell in SHELLS for c in READ_COMMANDS]
DENIED = (["Write", "Edit", "NotebookEdit", "WebFetch", "WebSearch", "Read", "Glob", "Grep", "Task"]
          + [f"{shell}({c})" for shell in SHELLS for c in DENIED_COMMANDS])

# The app's session reads the same playbook as every other Claude Code session
# (playbook.md, also `tgkit guide` and the plugin skill), plus the rules of the app.
APP_RULES = """
## In the TG Kit app (this session)

You answer inside the TG Kit window. Here these rules come before the ones above:

- Your only tool is the `tgkit` command, run through your shell tool (Bash or PowerShell,
  whichever you have). Never read, list or print files.
- This session is read-only. Never run sync, setup, add, `search --online`, or a task change
  (done, undo, reopen, drop, note, add), even when the user asks: tell them to use the window's
  buttons (Get new messages, Mark done). The tgkit you run refuses them anyway.
- If a chat has "stale": true, say its copy may be old and suggest the Get new messages button.
- Answer short and plain; never show commands.
"""
GUIDE = playbook.text() + APP_RULES

FALLBACK_HINTS = (
    ("unknown option", "compat"),
    ("not logged in", "login"), ("please run /login", "login"), ("invalid api key", "login"),
    ("authentication", "login"), ("oauth", "login"),
)


_DASHES = {0x2014: "-", 0x2013: "-"}  # em dash, en dash (code points: no dash in the source)


def plain(text: str) -> str:
    """The model sometimes writes em or en dashes although the guide asks it not to;
    the app shows a plain hyphen instead (same mapping for the streamed words and
    the final answer, so they match)."""
    return (text or "").translate(_DASHES)


def find_claude() -> list[str] | None:
    """The command that starts Claude Code, or None. TGKIT_CLAUDE overrides it (tests)."""
    override = os.environ.get("TGKIT_CLAUDE")
    if override:
        if override.lower() in ("0", "off", "none"):
            return None
        return [sys.executable, override] if override.endswith(".py") else [override]
    found = shutil.which("claude")
    return [found] if found else None


def _tgkit_dirs() -> list[str]:
    """Folders holding the tgkit command of THIS install, so Claude runs the same one."""
    exe = Path(sys.executable).resolve().parent
    dirs = [exe, exe / "Scripts", exe.parent / "bin"]
    return [str(d) for d in dirs if (d / "tgkit").exists() or (d / "tgkit.exe").exists()]


class ClaudeRunner:
    def __init__(self, cmd: list[str] | None = None, model: str | None = None):
        self.cmd = cmd if cmd is not None else find_claude()
        self.model = model or os.environ.get("TGKIT_CLAUDE_MODEL") or None
        self.compat = False  # an older claude without the newer flags

    @property
    def available(self) -> bool:
        return bool(self.cmd)

    # -------------------------------------------------------------- command

    def args(self, home: Path, session: str | None, guide_file: Path | None) -> list[str]:
        a = list(self.cmd) + ["-p", "--output-format", "stream-json", "--verbose"]
        if not self.compat:
            a += ["--include-partial-messages", "--tools", ",".join(SHELLS), "--permission-mode", "dontAsk",
                  "--safe-mode", "--strict-mcp-config"]
            if guide_file is not None:
                a += ["--append-system-prompt-file", str(guide_file)]
        a += ["--allowedTools", *ALLOWED, "--disallowedTools", *DENIED]
        if self.model:
            a += ["--model", self.model]
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
        """Events: session, token, step, step_done, tool_json, final, error."""
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
        env = dict(os.environ)
        env["TGKIT_HOME"] = str(home)
        env[READONLY_ENV] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        extra = _tgkit_dirs()
        if extra:
            env["PATH"] = os.pathsep.join(extra + [env.get("PATH", "")])
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
                if kind == "stream_event":
                    ev = msg.get("event") or {}
                    delta = ev.get("delta") or {}
                    if ev.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
                        streamed = True
                        yield {"type": "token", "text": plain(delta.get("text", ""))}
                elif kind == "assistant":
                    for block in (msg.get("message") or {}).get("content") or []:
                        if block.get("type") == "tool_use":
                            cmd = (block.get("input") or {}).get("command") or block.get("name")
                            tools[block.get("id")] = cmd
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
                        cmd = tools.get(block.get("tool_use_id")) or ""
                        if ok and "--json" in cmd:
                            try:
                                yield {"type": "tool_json", "command": cmd, "data": json.loads(body)}
                            except ValueError:
                                pass
                elif kind == "result":
                    final = msg
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait()
            t.join(timeout=2)

        if final and not final.get("is_error") and proc.returncode == 0:
            yield {"type": "final", "text": plain(final.get("result") or ""), "session": new_session}
            return
        detail = " ".join(err_tail) or (final or {}).get("result") or f"exit code {proc.returncode}"
        low = detail.lower()
        reason = next((r for k, r in FALLBACK_HINTS if k in low), "failed")
        message = {
            "login": "Claude is not signed in on this computer.",
            "compat": "This Claude Code is older than TG Kit expects.",
        }.get(reason, "Claude could not answer this time.")
        yield {"type": "error", "reason": reason, "message": message, "detail": detail[-400:]}
