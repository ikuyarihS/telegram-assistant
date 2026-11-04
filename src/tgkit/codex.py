"""GPT mode for the app: a headless OpenAI Codex CLI session (signed in with a ChatGPT
account) that answers with tgkit, beside Claude (claude.py) and Gemini (gemini.py).

Checked against Codex CLI 0.166.0 (npm @openai/codex: its --help, and a run against a
local stand-in for the model, so no OpenAI account was used):

- `codex exec --json -` runs headless with the question on stdin and prints one JSON
  event per line: thread.started (thread_id), item.started / item.completed with an
  item of type agent_message (text), mcp_tool_call (server, tool, arguments, result,
  error, status), command_execution, file_change, web_search, error; turn.completed
  (usage) and turn.failed (error). `codex exec resume <thread id> -` continues one.
- No shell, no file tools, no web: `--disable shell_tool` and `--disable unified_exec`
  remove the shell (a model that asks for it gets "unsupported call"), web_search is
  "disabled", and the other built-in tools (images, agents, goals, apps, browser,
  computer use, plugins, hooks) are switched off. What is left is tgkit's own MCP
  server (mcp.py: typed fields, an argv list, never a shell) and the CLI's readers of
  that same server's resources.
- `-c sandbox_mode="read-only"` and `-c approval_policy="never"`: nothing may write,
  and nothing is ever asked (a headless "ask" is "no"). tgkit's tools say they only
  read (readOnlyHint), which is what lets them run under "never".
- `--ignore-user-config` and `--ignore-rules`: the user's own Codex settings, MCP
  servers and rules are not loaded; the sign-in still is. The tgkit server comes in
  per run as `-c mcp_servers.tgkit...`. Nothing of the user's Codex setup is edited.
- Signed in: `codex login status` exits 0 ("Logged in ..."), 1 with "Not logged in".
  The answer of the CLI itself is used, never the existence of a file.
- On Windows npm's codex.js starts codex.exe as a child; the app runs codex.exe itself,
  so Stop ends the process that works.

If Codex is missing, not signed in, or fails, the caller answers offline.
"""

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

from . import ai, mcp, playbook
from . import gemini as _node  # finding Node.js and npm is the same for both npm CLIs
from .ai import plain

PACKAGE = "@openai/codex"
NODE_URL = _node.NODE_URL
MIN_NODE = 16  # the package's engines.node is ">=16"
NO_WINDOW = 0x08000000 if os.name == "nt" else 0
NEW_CONSOLE = 0x00000010

APP_RULES = ai.app_rules("the tools of the tgkit server")
GUIDE = playbook.text() + APP_RULES

# Built-in tools switched off for every run (names from `codex features list`, 0.166.0).
DISABLED_FEATURES = ("shell_tool", "unified_exec", "view_image", "multi_agent", "goals", "apps", "browser_use",
                     "browser_use_external", "computer_use", "image_generation", "plugins", "hooks",
                     "tool_suggest", "sleep_tool",
                     # Codex's own browser and automation (on by default in 0.166.0): the AI reads the
                     # browser only through tgkit's checked bridge, never through these.
                     "in_app_browser", "in_app_local_automation", "code_mode_host", "browser_annotation_api",
                     "browser_use_full_cdp_access")
# Items that mean something other than tgkit's tools ran: the run is stopped.
FORBIDDEN_ITEMS = ("command_execution", "file_change", "web_search", "collab_tool_call")
TARGETS = {("win32", "AMD64"): ("x86_64-pc-windows-msvc", "win32-x64"),
           ("win32", "ARM64"): ("aarch64-pc-windows-msvc", "win32-arm64"),
           ("darwin", "arm64"): ("aarch64-apple-darwin", "darwin-arm64"),
           ("darwin", "x86_64"): ("x86_64-apple-darwin", "darwin-x64"),
           ("linux", "x86_64"): ("x86_64-unknown-linux-musl", "linux-x64"),
           ("linux", "aarch64"): ("aarch64-unknown-linux-musl", "linux-arm64")}

FALLBACK_HINTS = (
    ("not logged in", "login"), ("401", "login"), ("unauthorized", "login"), ("log in again", "login"),
    ("refresh token", "login"), ("codex login", "login"),
    ("usage limit", "limit"), ("rate limit", "limit"), ("429", "limit"), ("quota", "limit"),
)
MESSAGES = {
    "login": "GPT (Codex) is not signed in on this computer.",
    "limit": "GPT has reached its limit for now.",
    "blocked": "GPT tried something Telegram Assistant does not allow, so it was stopped.",
    "missing": "GPT (Codex) could not start.",
}


# ------------------------------------------------------------------ finding it

def _exists(p) -> bool:
    try:
        return bool(p) and Path(p).is_file()
    except OSError:
        return False


def _native(pkg_dir: Path) -> Path | None:
    """codex.exe (codex) inside the npm package, where codex.js looks for it."""
    machine = os.environ.get("PROCESSOR_ARCHITECTURE", "") if os.name == "nt" else os.uname().machine
    plat = "win32" if os.name == "nt" else sys.platform
    target = TARGETS.get((plat, machine.upper() if os.name == "nt" else machine))
    if not target:
        return None
    triple, suffix = target
    exe = "codex.exe" if os.name == "nt" else "codex"
    for root in (pkg_dir.parent / f"codex-{suffix}", pkg_dir / "node_modules" / "@openai" / f"codex-{suffix}", pkg_dir):
        cand = root / "vendor" / triple / "bin" / exe
        if _exists(cand):
            return cand
    return None


def _package(shim: Path) -> Path | None:
    for cand in (shim.parent / "node_modules" / "@openai" / "codex", shim.resolve().parent.parent):
        if _exists(cand / "package.json") and _exists(cand / "bin" / "codex.js"):
            return cand
    return None


def find_codex() -> list[str] | None:
    """The command that starts Codex, or None. TGKIT_CODEX overrides it (tests, or "off")."""
    override = os.environ.get("TGKIT_CODEX")
    if override:
        if override.lower() in ("0", "off", "none"):
            return None
        return [sys.executable, override] if override.endswith(".py") else [override]
    shim = shutil.which("codex")
    if not shim:
        names = ("codex.cmd", "codex") if os.name == "nt" else ("codex",)
        shim = next((str(d / n) for d in _node.npm_dirs() for n in names if _exists(d / n)), None)
    if not shim:
        return None
    pkg = _package(Path(shim))
    if pkg:
        native = _native(pkg)
        if native:
            return [str(native)]
        node = _node.find_node()
        if node:
            return [node, str(pkg / "bin" / "codex.js")]
    return [shim]


def _env(cmd: list[str]) -> dict:
    env = dict(os.environ)
    exe = Path(cmd[0])
    if exe.name.lower().startswith("codex") and "vendor" in exe.parts:  # what codex.js sets for it
        pkg = next((p for p in exe.parents if (p / "package.json").exists() and p.name.startswith("codex")), None)
        env["CODEX_MANAGED_BY_NPM"] = "1"
        if pkg is not None:
            env["CODEX_MANAGED_PACKAGE_ROOT"] = str(pkg)
    return env


def _run(cmd: list[str], *args: str, timeout: int = 30) -> tuple[int, str]:
    """A quick command (version, login status). Never sends a prompt."""
    try:
        done = subprocess.run([*cmd, *args], stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout,
                              creationflags=NO_WINDOW, env=_env(cmd))
    except (OSError, subprocess.TimeoutExpired):
        return -1, ""
    return done.returncode, ((done.stdout or b"") + (done.stderr or b"")).decode("utf-8", "replace")


def status(cmd: list[str] | None = None, home: Path | None = None) -> dict:
    """Codex on this computer, in plain states: no_node, missing, not_logged_in, connected."""
    cmd = cmd if cmd is not None else find_codex()
    node = _node.node_info()
    node["ok"] = bool(node["found"] and node["version"] and int(node["version"].split(".")[0]) >= MIN_NODE)
    out = {"state": "missing", "installed": False, "version": None, "path": None, "node": node,
           "logged_in": None, "how": None, "package": PACKAGE, "node_url": NODE_URL,
           "can_install": False, "winget": _node.winget()}
    if not cmd:
        if not node["ok"]:
            out["state"] = "no_node"
            out["fix"] = ["GPT needs Node.js. Press Install Node.js and click Next until Finish.",
                          "Then press Install GPT.", "Then press Sign in to ChatGPT."]
            return out
        out["can_install"] = bool(_node.find_npm())
        out["fix"] = ["Press Install GPT (about a minute).", "Then press Sign in to ChatGPT.",
                      "Come back and press Check again."]
        return out
    out["installed"], out["path"] = True, cmd[-1]
    code, text = _run(cmd, "--version")
    m = re.search(r"\d+\.\d+\.\d+", text)
    out["version"] = m.group(0) if m else None
    if code != 0 and not out["version"]:
        out["fix"] = ["GPT (Codex) is installed but does not start. Press Install GPT to install it again."]
        out["can_install"] = node["ok"] and bool(_node.find_npm())
        return out
    code, text = _run(cmd, "login", "status")
    low = text.lower()
    out["logged_in"] = code == 0 and "not logged in" not in low
    if out["logged_in"]:
        out["how"] = "chatgpt" if "chatgpt" in low else ("api key" if "api key" in low else None)
    out["state"] = "connected" if out["logged_in"] else "not_logged_in"
    out["fix"] = [] if out["logged_in"] else [
        "Press Sign in to ChatGPT. A window opens and then a ChatGPT page in your browser.",
        "Sign in with your ChatGPT account (Plus, Pro, Business or Enterprise).",
        "When the page says you are signed in, close that window and press Check again."]
    return out


def login_argv(cmd: list[str]) -> list[str]:
    return [*cmd, "login"]


def from_codex(u) -> dict | None:
    """The usage of one answer, from turn.completed (input_tokens includes the cached ones)."""
    if not isinstance(u, dict) or not u:
        return None

    def n(k):
        try:
            return max(0, int(u.get(k) or 0))
        except (TypeError, ValueError):
            return 0
    cached = n("cached_input_tokens")
    return {"input": max(0, n("input_tokens") - cached), "output": n("output_tokens"),
            "cache_read": cached, "cache_write": n("cache_write_input_tokens")}


def _toml(value) -> str:
    return json.dumps(value, ensure_ascii=False)  # a JSON string or list of strings is valid TOML


# ------------------------------------------------------------------ the runner

class CodexRunner:
    name = "gpt"

    def __init__(self, cmd: list[str] | None = None, model: str | None = None):
        self.cmd = cmd if cmd is not None else find_codex()
        self.model = model or os.environ.get("TGKIT_CODEX_MODEL") or None
        self.proc = None

    @property
    def available(self) -> bool:
        return bool(self.cmd)

    def args(self, home: Path, session: str | None, work: Path) -> list[str]:
        from . import connectors  # noqa: PLC0415

        srv = mcp.server_command(home, browser=connectors.browser_on(home), who="gpt")
        a = list(self.cmd) + ["exec"]
        if session:
            a += ["resume", session]
        a += ["--json", "--skip-git-repo-check", "--ignore-user-config", "--ignore-rules",
              "-c", 'sandbox_mode="read-only"', "-c", 'approval_policy="never"', "-c", 'web_search="disabled"',
              "-c", 'shell_environment_policy.inherit="none"']
        for feature in DISABLED_FEATURES:
            a += ["--disable", feature]
        a += ["-c", f"mcp_servers.{mcp.SERVER}.command={_toml(srv['command'])}",
              "-c", f"mcp_servers.{mcp.SERVER}.args={_toml(srv['args'])}",
              "-c", f"mcp_servers.{mcp.SERVER}.env={{{', '.join(f'{k} = {_toml(v)}' for k, v in srv['env'].items())}}}",
              "-c", f"mcp_servers.{mcp.SERVER}.tool_timeout_sec={mcp.TIMEOUT}"]
        if not session:
            a += ["-C", str(work)]
        if self.model:
            a += ["-m", self.model]
        return a + ["-"]

    def _prompt(self, text: str, session: str | None, chats: list[str]) -> str:
        if session:
            return text + "\n"
        today = datetime.now().strftime("%Y-%m-%d")
        names = ", ".join(chats) if chats else "(none yet)"
        return f"{GUIDE}\nToday is {today}. The user's chats: {names}.\n\nUser question:\n{text}\n"

    def stream(self, text: str, home: Path, session: str | None = None,
               chats: list[str] | None = None) -> Iterator[dict]:
        """The events of ai.py: session, model, token, step, step_done, tool_json, usage, final, error."""
        home = Path(home)
        work = home / "app" / "gpt"
        work.mkdir(parents=True, exist_ok=True)
        env = {**_env(self.cmd), **ai.run_env(home), "NO_COLOR": "1"}
        try:
            proc = subprocess.Popen(self.args(home, session, work), cwd=str(work), env=env,
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    creationflags=NO_WINDOW)
        except OSError as exc:
            yield {"type": "error", "reason": "missing", "message": MESSAGES["missing"], "detail": str(exc)}
            return
        self.proc = proc
        err_tail: deque = deque(maxlen=40)

        def drain():
            for raw in proc.stderr:
                err_tail.append(raw.decode("utf-8", "replace").rstrip())
        t = threading.Thread(target=drain, daemon=True)
        t.start()
        try:
            proc.stdin.write(self._prompt(text, session, chats or []).encode("utf-8"))
            proc.stdin.close()
        except OSError:
            pass
        if self.model:
            yield {"type": "model", "model": self.model}

        said: list[str] = []
        new_session, done_ok, problems, blocked, used = session, False, [], None, None
        try:
            for raw in proc.stdout:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("{"):
                    if line:
                        err_tail.append(line)
                    continue
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                kind = msg.get("type")
                item = msg.get("item") if isinstance(msg.get("item"), dict) else {}
                itype = item.get("type")
                if kind == "thread.started" and msg.get("thread_id"):
                    new_session = str(msg["thread_id"])
                    yield {"type": "session", "id": new_session}
                elif kind in ("item.started", "item.completed") and itype in FORBIDDEN_ITEMS:
                    blocked = f"{itype}: {str(item.get('command') or item.get('query') or '')[:200]}"
                    break
                elif kind in ("item.started", "item.completed") and itype == "mcp_tool_call":
                    tool = str(item.get("tool") or "")
                    if item.get("server") != mcp.SERVER or not mcp.known(tool):
                        if kind == "item.completed" and item.get("status") == "completed":
                            blocked = f"{item.get('server')}.{tool}"
                            break
                        continue
                    args = item.get("arguments") if isinstance(item.get("arguments"), dict) else {}
                    cmd = mcp.describe(tool, args)
                    if kind == "item.started":
                        yield {"type": "step", "id": item.get("id"), "command": cmd}
                        continue
                    result = item.get("result") if isinstance(item.get("result"), dict) else {}
                    body = "".join(str(c.get("text") or "") for c in result.get("content") or []
                                   if isinstance(c, dict))
                    ok = item.get("status") == "completed"
                    note = "" if ok else str((item.get("error") or {}).get("message") or body)[:300]
                    yield {"type": "step_done", "id": item.get("id"), "ok": ok, "note": note}
                    said = []
                    if ok and "--json" in cmd:
                        data = _node._json_in(body)
                        if data is not None:
                            yield {"type": "tool_json", "command": cmd, "data": data}
                elif kind == "item.completed" and itype == "agent_message":
                    words = plain(str(item.get("text") or ""))
                    if words:
                        if said:
                            words = "\n\n" + words
                        said.append(words)
                        yield {"type": "token", "text": words}
                elif kind == "item.completed" and itype == "error":
                    problems.append(str(item.get("message") or ""))
                elif kind == "turn.completed":
                    done_ok = True
                    used = from_codex(msg.get("usage"))
                    if used:
                        if self.model:
                            used["models"] = [self.model]
                        yield {"type": "usage", "usage": used}
                elif kind == "turn.failed":
                    problems.append(str((msg.get("error") or {}).get("message") or "the turn failed"))
                elif kind == "error":
                    problems.append(str(msg.get("message") or ""))
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait()
            t.join(timeout=2)

        if blocked:
            yield {"type": "error", "reason": "blocked", "message": MESSAGES["blocked"], "detail": blocked[-400:]}
            return
        if done_ok and proc.returncode == 0 and said:
            yield {"type": "final", "text": "".join(said).strip(), "session": new_session}
            return
        detail = " ".join([p for p in problems if p] + list(err_tail)) or f"exit code {proc.returncode}"
        low = detail.lower()
        reason = next((r for k, r in FALLBACK_HINTS if k in low), "failed")
        yield {"type": "error", "reason": reason, "message": MESSAGES.get(reason, "GPT could not answer this time."),
               "detail": detail[-400:]}
