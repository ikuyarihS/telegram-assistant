"""Gemini mode for the app: a headless Gemini CLI session that answers with tgkit,
for people who sign in with a Google account instead of having Claude Code.

Checked against Gemini CLI 0.66.0 (its --help and bundled docs):

- `gemini -p <text> -o stream-json` runs headless; the question also goes on stdin
  (-p is appended to stdin). Events, one JSON per line: init (session_id, model),
  message (role assistant, delta), tool_use (tool_name, tool_id, parameters),
  tool_result (tool_id, status, output, error), error, result (status, stats).
- `--resume <session id>` continues a session of the same folder, so every run
  uses one fixed work folder in the data folder.
- No shell: tgkit's read commands are the tools of a small MCP server run by
  Telegram Assistant itself (mcp.py, `python -m tgkit.mcp`, an argv list, typed
  JSON fields), so no text from the model ever reaches PowerShell, cmd or bash.
  Each run writes the server into the work folder's `.gemini/settings.json`
  (workspace settings; `--skip-trust` trusts that folder for the run, which a
  stdio MCP server needs) and passes `--allowed-mcp-server-names tgkit`, so the
  user's own MCP servers stay off. The user's own Gemini settings are never edited.
- Tools are ruled by the policy engine. Each run passes Telegram Assistant's own
  policy file (`--admin-policy`, the top tier, and `--policy` as well, in case an
  administrator's system policy makes the CLI skip supplemental admin files):
  only the tools of the tgkit MCP server are allowed (mcpName = "tgkit"); the
  shell and every other tool (files, web, other MCP servers, skills, agents) are
  denied and so hidden from the model. The tgkit it runs is read-only too
  (TGKIT_READONLY).
- Not signed in: it exits with code 41 and "Please set an Auth method". The login
  token sits in the system keychain, so "signed in" here is the CLI's own
  condition for headless runs (an auth type in ~/.gemini/settings.json and, for a
  Google login, the account in google_accounts.json, or GEMINI_API_KEY), until an
  answer fails for sign-in.
- GEMINI_CLI_NO_RELAUNCH: it does not start a second node, so Stop ends the
  process the app started.

If Gemini is missing, not signed in, or fails, the caller answers offline.
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

from . import ai, mcp, playbook, usage
from .ai import plain
from .store import atomic_write

PACKAGE = "@google/gemini-cli"
NODE_URL = "https://nodejs.org/en/download"
MIN_NODE = 20  # the package's engines.node is ">=20"
SHELL_TOOL = "run_shell_command"
AUTH_EXIT = 41  # FATAL_AUTHENTICATION_ERROR
NO_WINDOW = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
NEW_CONSOLE = 0x00000010  # CREATE_NEW_CONSOLE
WINGET_NODE = ["winget", "install", "-e", "--id", "OpenJS.NodeJS.LTS",
               "--accept-source-agreements", "--accept-package-agreements"]

APP_RULES = ai.app_rules("the tools of the tgkit server")
GUIDE = playbook.text() + APP_RULES
# The question goes on stdin; -p only starts headless mode and is added after it.
PROMPT_FLAG = "Answer the request above."

FALLBACK_HINTS = (
    ("auth method", "login"), ("not signed in", "login"), ("login required", "login"),
    ("oauth", "login"), ("credentials", "login"), ("unauthenticated", "login"), ("api key not valid", "login"),
    ("quota", "limit"), ("resource_exhausted", "limit"), ("rate limit", "limit"), ("429", "limit"),
)
MESSAGES = {
    "login": "Gemini is not signed in on this computer.",
    "limit": "Gemini has reached its limit for now.",
    "blocked": "Gemini tried something Telegram Assistant does not allow, so it was stopped.",
    "missing": "Gemini could not start.",
}


def policy_text() -> str:
    """The policy file of every Gemini run (TOML, Gemini CLI policy engine): the tools of
    the tgkit MCP server are allowed, the shell and everything else denied. Every rule
    has a toolName: 0.66.0 drops the whole file for an mcpName rule without one (its
    docs say otherwise; checked with the CLI's own loader)."""
    return f"""# Telegram Assistant: the only tools of the Gemini session are tgkit's read commands,
# served by Telegram Assistant itself (no shell). Written for each answer; your own
# Gemini settings are not changed.

[[rule]]
toolName = "{SHELL_TOOL}"
decision = "deny"
priority = 999
denyMessage = "Telegram Assistant gives Gemini no shell. Use the tgkit tools."

[[rule]]
toolName = "{SHELL_TOOL}"
commandRegex = ".*"
decision = "deny"
priority = 999
denyMessage = "Telegram Assistant gives Gemini no shell. Use the tgkit tools."

[[rule]]
toolName = "*"
mcpName = "{mcp.SERVER}"
decision = "allow"
priority = 500

[[rule]]
toolName = "*"
decision = "deny"
priority = 100
denyMessage = "Only tgkit read commands are allowed in Telegram Assistant."

[[rule]]
toolName = "*"
mcpName = "*"
decision = "deny"
priority = 100
"""


def settings_for(home: Path) -> dict:
    """The work folder's .gemini/settings.json: the tgkit MCP server and nothing else.
    Workspace settings, so an administrator's system settings still come first. With the
    browser on (connectors.py), the server also passes on the browser's reading tools."""
    from . import connectors  # noqa: PLC0415

    browser = connectors.browser_on(home)
    tools = list(mcp.TOOLS) + (list(connectors.BROWSER_ALLOWED) if browser else [])
    return {"mcpServers": {mcp.SERVER: {**mcp.server_command(home, browser=browser), "timeout": mcp.TIMEOUT * 1000,
                                        "includeTools": tools}}}


# ------------------------------------------------------------------ finding it

def _exists(p) -> bool:
    try:
        return bool(p) and Path(p).is_file()
    except OSError:
        return False


def _from_override(value: str) -> list[str] | None:
    if value.lower() in ("0", "off", "none"):
        return None
    if value.endswith(".py"):
        return [sys.executable, value]
    if value.endswith((".js", ".mjs")):
        node = find_node()
        return [node, value] if node else None
    return [value]


def node_dirs() -> list[Path]:
    """Where Node.js lands when it is installed while the app is open (PATH is old then)."""
    out = []
    if os.name == "nt":
        for var in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
            if os.environ.get(var):
                out.append(Path(os.environ[var]) / "nodejs")
        if os.environ.get("LOCALAPPDATA"):
            out.append(Path(os.environ["LOCALAPPDATA"]) / "Programs" / "nodejs")
    else:
        out += [Path("/usr/local/bin"), Path("/opt/homebrew/bin")]
    return out


def npm_dirs() -> list[Path]:
    """Where `npm install -g` puts the gemini command."""
    out = []
    if os.name == "nt" and os.environ.get("APPDATA"):
        out.append(Path(os.environ["APPDATA"]) / "npm")
    elif os.name != "nt":
        out += [Path.home() / ".npm-global" / "bin", Path("/usr/local/bin"), Path("/opt/homebrew/bin")]
    return out


def find_node() -> str | None:
    override = os.environ.get("TGKIT_NODE")
    if override:
        return None if override.lower() in ("0", "off", "none") else override
    found = shutil.which("node")
    if found:
        return found
    name = "node.exe" if os.name == "nt" else "node"
    return next((str(d / name) for d in node_dirs() if _exists(d / name)), None)


def find_npm() -> list[str] | None:
    override = os.environ.get("TGKIT_NPM")
    if override:
        return _from_override(override)
    node = find_node()
    names = ("npm.cmd", "npm") if os.name == "nt" else ("npm",)
    if node:
        for n in names:
            if _exists(Path(node).parent / n):
                return [str(Path(node).parent / n)]
    found = shutil.which("npm")
    return [found] if found else None


def _entry(shim: Path) -> Path | None:
    """The JavaScript file behind npm's gemini command (package.json "bin")."""
    try:
        real = shim.resolve()
    except OSError:
        real = shim
    if real.suffix in (".js", ".mjs"):
        return real
    pkg_dir = shim.parent / "node_modules" / "@google" / "gemini-cli"
    try:
        pkg = json.loads((pkg_dir / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    rel = pkg.get("bin")
    rel = rel.get("gemini") if isinstance(rel, dict) else rel
    if isinstance(rel, str) and _exists(pkg_dir / rel):
        return pkg_dir / rel
    return None


def find_gemini() -> list[str] | None:
    """The command that starts the Gemini CLI, or None. TGKIT_GEMINI overrides it
    (tests, or "off"). npm's gemini.cmd goes through cmd.exe, which Stop could not
    end cleanly, so the app runs node with the CLI's own script when it finds it."""
    override = os.environ.get("TGKIT_GEMINI")
    if override:
        return _from_override(override)
    shim = shutil.which("gemini")
    if not shim:
        names = ("gemini.cmd", "gemini") if os.name == "nt" else ("gemini",)
        shim = next((str(d / n) for d in npm_dirs() for n in names if _exists(d / n)), None)
    if not shim:
        return None
    entry = _entry(Path(shim))
    node = find_node()
    if not node and _exists(Path(shim).parent / "node.exe"):
        node = str(Path(shim).parent / "node.exe")
    if entry and node:
        return [node, str(entry)]
    return [shim]


def _run(cmd: list[str], *args: str, timeout: int = 30, env: dict | None = None) -> tuple[int, str]:
    """A quick command (version). Never sends a prompt."""
    try:
        done = subprocess.run([*cmd, *args], stdin=subprocess.DEVNULL, capture_output=True,
                              timeout=timeout, creationflags=NO_WINDOW, env=env)
    except (OSError, subprocess.TimeoutExpired):
        return -1, ""
    return done.returncode, (done.stdout or b"").decode("utf-8", "replace") + (done.stderr or b"").decode(
        "utf-8", "replace")


def node_info() -> dict:
    node = find_node()
    out = {"found": bool(node), "path": node, "version": None, "ok": False}
    if node:
        code, text = _run([node], "--version")
        m = re.search(r"v?(\d+)\.(\d+)\.(\d+)", text)
        if code == 0 and m:
            out["version"] = ".".join(m.groups())
            out["ok"] = int(m.group(1)) >= MIN_NODE
    return out


def winget() -> bool:
    return os.name == "nt" and bool(shutil.which("winget"))


# ------------------------------------------------------------------ signed in

def gemini_dir() -> Path:
    """~/.gemini, as the CLI finds it (GEMINI_CLI_HOME moves it)."""
    base = os.environ.get("GEMINI_CLI_HOME")
    return Path(base) / ".gemini" if base else Path.home() / ".gemini"


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def selected_auth() -> str | None:
    """The auth type the CLI uses headless: security.auth.selectedType in its settings
    (read only; a settings file may hold comments, so the key is found by pattern),
    else an API key in the environment."""
    try:
        text = (gemini_dir() / "settings.json").read_text(encoding="utf-8")
    except OSError:
        text = ""
    m = re.search(r'"selectedType"\s*:\s*"([^"]+)"', text) or re.search(r'"selectedAuthType"\s*:\s*"([^"]+)"', text)
    if m:
        return m.group(1)
    if os.environ.get("GEMINI_API_KEY"):
        return "gemini-api-key"
    return None


def google_account() -> str | None:
    """The Google account the CLI signed in with (google_accounts.json, "active")."""
    data = _read_json(gemini_dir() / "google_accounts.json")
    active = data.get("active") if isinstance(data, dict) else None
    return active if isinstance(active, str) and "@" in active else None


def _signature() -> str:
    """Changes when the user signs in again (settings or account file rewritten)."""
    parts = []
    for name in ("settings.json", "google_accounts.json", "oauth_creds.json"):
        try:
            parts.append(str((gemini_dir() / name).stat().st_mtime_ns))
        except OSError:
            parts.append("-")
    return ":".join(parts) + ":" + ("key" if os.environ.get("GEMINI_API_KEY") else "")


def _marker(home: Path | None) -> Path | None:
    return Path(home) / "app" / "gemini" / "signin-failed.json" if home else None


def mark_signin_failed(home: Path | None) -> None:
    m = _marker(home)
    if m:
        atomic_write(m, json.dumps({"signature": _signature(), "at": datetime.now().isoformat()}))


def clear_signin_failed(home: Path | None) -> None:
    m = _marker(home)
    if m and m.exists():
        try:
            m.unlink()
        except OSError:
            pass


def _signin_failed(home: Path | None) -> bool:
    m = _marker(home)
    data = _read_json(m) if m else None
    return isinstance(data, dict) and data.get("signature") == _signature()


def status(cmd: list[str] | None = None, home: Path | None = None) -> dict:
    """The Gemini CLI on this computer, in plain states:
    no_node, missing, not_logged_in, connected."""
    cmd = cmd if cmd is not None else find_gemini()
    node = node_info()
    out = {"state": "missing", "installed": False, "version": None, "path": None, "node": node,
           "account": None, "auth_method": None, "logged_in": None, "package": PACKAGE,
           "node_url": NODE_URL, "can_install": False, "winget": winget()}
    if not cmd:
        if not node["ok"]:
            out["state"] = "no_node"
            too_old = node["found"] and node["version"]
            out["fix"] = [
                (f"Node.js {node['version']} is too old for Gemini: install the newer one (LTS)."
                 if too_old else "Gemini needs Node.js. Press Install Node.js (or get the LTS installer at "
                 + NODE_URL + ") and click Next until Finish."),
                "Then press Install Gemini.", "Then press Sign in to Google."]
            return out
        out["can_install"] = bool(find_npm())
        out["fix"] = ["Press Install Gemini (about a minute).", "Then press Sign in to Google.",
                      "Come back and press Check again."]
        return out
    out["installed"], out["path"] = True, cmd[-1]
    env = dict(os.environ, GEMINI_CLI_NO_RELAUNCH="true")
    code, text = _run(cmd, "--version", env=env)
    m = re.search(r"\d+\.\d+\.\d+", text)
    out["version"] = m.group(0) if m else None
    if code != 0 and not out["version"]:
        out["fix"] = ["Gemini is installed but does not start. Press Install Gemini to install it again."]
        out["can_install"] = node["ok"] and bool(find_npm())
        return out
    auth = selected_auth()
    out["auth_method"] = auth
    out["account"] = google_account() if auth == "oauth-personal" else None
    signed = bool(auth) and (auth != "oauth-personal" or bool(out["account"])
                             or _exists(gemini_dir() / "oauth_creds.json"))
    out["logged_in"] = signed and not _signin_failed(home)
    out["state"] = "connected" if out["logged_in"] else "not_logged_in"
    out["fix"] = [] if out["logged_in"] else [
        "Press Sign in to Google. A window opens.",
        "Choose Sign in with Google, then pick your Google account in the browser page that opens.",
        "When it says you are signed in, close that window and press Check again."]
    return out


def login_argv(cmd: list[str]) -> list[str]:
    """The interactive CLI: on its first start it asks how to sign in."""
    return list(cmd)


# ------------------------------------------------------------------ the runner

class GeminiRunner:
    name = "gemini"

    def __init__(self, cmd: list[str] | None = None, model: str | None = None):
        self.cmd = cmd if cmd is not None else find_gemini()
        self.model = model or os.environ.get("TGKIT_GEMINI_MODEL") or None
        self.proc = None

    @property
    def available(self) -> bool:
        return bool(self.cmd)

    def args(self, home: Path, session: str | None, policy: Path) -> list[str]:
        a = list(self.cmd) + ["-p", PROMPT_FLAG, "-o", "stream-json", "--approval-mode", "default",
                              "--admin-policy", str(policy), "--policy", str(policy), "--skip-trust",
                              "--allowed-mcp-server-names", mcp.SERVER]
        if self.model:
            a += ["-m", self.model]
        if session:
            a += ["--resume", session]
        return a

    def env(self, home: Path) -> dict:
        env = ai.run_env(home)
        env["GEMINI_CLI_NO_RELAUNCH"] = "true"
        # The work folder's own settings (the tgkit server) load only in a trusted folder;
        # --skip-trust alone comes too late for them in 0.66.0 (the tool was "not found").
        env["GEMINI_CLI_TRUST_WORKSPACE"] = "true"
        env["NO_COLOR"] = "1"
        return env

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
        work = home / "app" / "gemini"
        work.mkdir(parents=True, exist_ok=True)
        policy = work / "tgkit-policy.toml"
        atomic_write(policy, policy_text())
        (work / ".gemini").mkdir(exist_ok=True)
        atomic_write(work / ".gemini" / "settings.json", json.dumps(settings_for(home), indent=2))
        try:
            proc = subprocess.Popen(
                self.args(home, session, policy), cwd=str(work), env=self.env(home),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=NO_WINDOW)
        except OSError as exc:
            yield {"type": "error", "reason": "missing", "message": MESSAGES["missing"], "detail": str(exc)}
            return
        self.proc = proc  # the one process Stop ends (no relaunch, no cmd.exe in between)
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

        tools: dict = {}
        said: list[str] = []  # the words since the last tool result: the answer
        final, new_session, problems, blocked = None, None, [], None
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
                if kind == "init":
                    if msg.get("session_id"):
                        new_session = str(msg["session_id"])
                        yield {"type": "session", "id": new_session}
                    if msg.get("model"):
                        yield {"type": "model", "model": str(msg["model"])}
                elif kind == "message" and msg.get("role") == "assistant":
                    words = plain(str(msg.get("content") or ""))
                    if words:
                        said.append(words)
                        yield {"type": "token", "text": words}
                elif kind == "tool_use":
                    params = msg.get("parameters") or {}
                    name = str(msg.get("tool_name") or "")
                    tool = mcp.tool_of(name)
                    if tool:
                        cmd = mcp.describe(tool, params)
                    else:
                        cmd = str(params.get("command") or name) if isinstance(params, dict) else name
                    tools[msg.get("tool_id")] = (tool, name, cmd)
                    yield {"type": "step", "id": msg.get("tool_id"), "command": cmd}
                elif kind == "tool_result":
                    tool, name, cmd = tools.get(msg.get("tool_id"), (None, "", ""))
                    ok = msg.get("status") == "success"
                    body = str(msg.get("output") or "")
                    note = "" if ok else str((msg.get("error") or {}).get("message") or body)[:300]
                    yield {"type": "step_done", "id": msg.get("tool_id"), "ok": ok, "note": note}
                    said = []
                    if ok and not tool:
                        # A tool outside the tgkit server ran (the shell, a file tool): the CLI's
                        # policy should have refused it. Stop now.
                        blocked = f"{name}: {cmd}"
                        break
                    if ok and "--json" in cmd:
                        data = _json_in(body)
                        if data is not None:
                            yield {"type": "tool_json", "command": cmd, "data": data}
                elif kind == "error":
                    if msg.get("message"):
                        problems.append(str(msg["message"]))
                elif kind == "result":
                    final = msg
                    used = usage.from_gemini(msg.get("stats"))
                    if used:
                        yield {"type": "usage", "usage": used}
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait()
            t.join(timeout=2)

        if blocked:
            yield {"type": "error", "reason": "blocked", "message": MESSAGES["blocked"], "detail": blocked[-400:]}
            return
        if final and final.get("status") == "success" and proc.returncode == 0:
            clear_signin_failed(home)
            yield {"type": "final", "text": "".join(said).strip(), "session": new_session}
            return
        detail = " ".join([*problems, *err_tail]) or f"exit code {proc.returncode}"
        low = detail.lower()
        reason = "login" if proc.returncode == AUTH_EXIT else next((r for k, r in FALLBACK_HINTS if k in low), "failed")
        if reason == "login":
            mark_signin_failed(home)
        yield {"type": "error", "reason": reason, "message": MESSAGES.get(reason, "Gemini could not answer this time."),
               "detail": detail[-400:]}


def _json_in(body: str):
    """The JSON a tgkit --json command printed; the CLI may wrap the output in a few lines."""
    try:
        return json.loads(body)
    except ValueError:
        pass
    start, end = body.find("{"), body.rfind("}")
    if 0 <= start < end:
        try:
            return json.loads(body[start:end + 1])
        except ValueError:
            return None
    return None
