"""Connectors: more than Telegram for search, summary, tasks and the AI answers.

1. The browser (WhatsApp Web, Zalo Web, any site the user is signed in to): when the user
   turns on "Let the AI read your browser" (after a plain warning they must tick), the
   AI reads the user's OWN everyday Chrome or Edge through a browser extension, the way
   Claude Code uses Claude in Chrome:
   - Claude: Claude Code's own `--chrome` (the Claude in Chrome extension). claude.py
     allows only its reading tools (claude.CHROME_ALLOWED) and denies the rest.
   - Gemini and GPT: the Playwright MCP bridge (`@playwright/mcp --extension` and the
     Playwright Extension in the browser). Neither CLI gets the bridge itself: tgkit's
     own MCP server (mcp.py) starts it and passes on only the reading tools, each call
     checked here (browser_check): no typing, no keys, no forms, no scripts, no files,
     no uploads, a click only on an element of the last page the AI read (its `ref`)
     that is not a send / delete / call / settings button, judged by the page's own
     words for that element, not the AI's; pages only on the web (never this computer),
     never a link that sends or shares. Prompt text in a page ("send X to Y") cannot
     make anything type or send: those tools are not there.
   - Nothing of the user's own Claude, Gemini or Codex settings is changed; it is set per
     answer. Off by default; turning it off takes effect at the next answer.

2. MCP connectors (advanced): the user adds an MCP server (a command, or an https URL,
   or http on this computer). The app lists its tools; the user ticks the ones the AI
   may use. A tool that looks like it changes something (by its name, or its own
   readOnlyHint false / destructiveHint true) can never be ticked. The AI reaches them
   only through tgkit's own MCP server (mcp.py: connector_tools, connector_call), so
   no provider ever gets the user's server in its own config, and every call is
   checked again here against the live tool list: unticked, gone or now
   write-looking = refused.
"""

import contextlib
import ipaddress
import json
import os
import queue
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import __version__
from .errors import UserError, localize
from .store import SyncLock, read_json, write_json

FILE = "connectors.json"
NO_WINDOW = 0x08000000 if os.name == "nt" else 0

# ------------------------------------------------------------------ the browser

WARNING = ("When this is on, the AI can read the web pages open in your own Chrome or Edge, including sites you "
           "are signed in to, such as WhatsApp Web or Zalo. It can open pages, read them and click to open a chat; "
           "it cannot type, send, post or change settings. WhatsApp and Zalo do not officially allow other "
           "programs to read your chats and could limit or block an account that does it; use it gently and at "
           "your own risk. Opening a chat marks it as read on your phone too. You can turn this off at any time.")
CLAUDE_EXTENSION_URL = "https://claude.ai/chrome"  # the link Claude Code 2.1.317 itself gives
BRIDGE_PACKAGE = "@playwright/mcp"
BRIDGE_EXTENSION_URL = ("https://chromewebstore.google.com/detail/playwright-extension/"
                        "mmlmfjhmonkocbjadbfplnigmagldckm")  # from @playwright/mcp 0.0.84's own source
# Playwright MCP 0.0.84 with --extension (its tools/list). Allowed: reading, opening a
# page, one plain click. Denied: everything that types, presses keys, fills or uploads,
# runs code, writes files, closes or reshapes the browser, or reads its traffic. A tool
# in neither list (a newer bridge) is the third bucket: denied too (browser_buckets).
BROWSER_ALLOWED = ("browser_navigate", "browser_navigate_back", "browser_snapshot", "browser_take_screenshot",
                   "browser_find", "browser_wait_for", "browser_tabs", "browser_click")
BROWSER_DENIED = ("browser_type", "browser_press_key", "browser_fill_form", "browser_select_option",
                  "browser_file_upload", "browser_drop", "browser_drag", "browser_evaluate",
                  "browser_run_code_unsafe", "browser_handle_dialog", "browser_close", "browser_resize",
                  "browser_emulate_media", "browser_hover", "browser_console_messages",
                  "browser_network_requests", "browser_network_request")
TAB_ACTIONS = ("list", "select", "new")
# A click on an element described with one of these is refused (send buttons, menus that
# change or delete things, calls). English and Vietnamese.
CLICK_REFUSED = ("send", "gửi", "delete", "xóa", "xoá", "remove", "block", "chặn", "report", "báo cáo",
                 "leave", "rời", "archive", "lưu trữ", "mute", "tắt thông báo", "clear", "pin", "ghim", "forward",
                 "chuyển tiếp", "reply", "trả lời", "react", "like", "thả", "call", "gọi", "video", "setting",
                 "cài đặt", "log out", "logout", "đăng xuất", "attach", "đính kèm", "upload", "share", "chia sẻ",
                 "post", "đăng", "submit", "pay", "buy", "mua", "confirm", "xác nhận", "accept", "đồng ý",
                 "edit", "sửa", "recall", "thu hồi", "install", "download", "tải", "emoji", "sticker",
                 "voice", "ghi âm", "record", "add", "thêm", "new chat", "invite", "mời")

def browser_buckets(names) -> dict:
    """The bridge's tools in three groups: allowed, denied, and the third bucket (in
    neither list), which is denied as well and shown, so a new tool is never let through."""
    names = [str(n) for n in names]
    return {"allowed": [n for n in names if n in BROWSER_ALLOWED],
            "denied": [n for n in names if n in BROWSER_DENIED],
            "unknown": [n for n in names if n not in BROWSER_ALLOWED and n not in BROWSER_DENIED]}


# A link like web.whatsapp.com/send?text=..., wa.me/1?text=..., a share or compose page:
# it fills or sends a message, so the AI never opens it.
SEND_PATH = re.compile(r"(?:^|/)(?:send|share|sharer|intent|compose|post|submit|forward)(?:/|\.|$)", re.I)
SEND_QUERY = frozenset(("text", "body", "message", "msg", "content", "caption", "status", "tweet", "share"))
REF = re.compile(r"(?:f\d{1,6})?e\d{1,9}")  # Playwright's own test for a ref: /^(f\d+)?e\d+$/
KEEPS_PAGE = ("browser_take_screenshot", "browser_find")  # these do not change the page the refs belong to


def _web_url(url) -> str:
    """A page on the web: http(s), a public host. Never file:, javascript:, the browser's
    own pages, this computer (the app's own server), a private network address, or a
    link that sends or shares a message."""
    u = urlparse(str(url or "").strip())
    host = (u.hostname or "").lower()
    if u.scheme not in ("http", "https") or not host:
        raise UserError("The AI may only open web pages (http or https).", "")
    if SEND_PATH.search(u.path or "") or any(k.lower() in SEND_QUERY for k in parse_qs(u.query, True)):
        raise UserError("The AI may not open a link that writes, sends or shares a message.", "")
    if host in ("localhost",) or host.endswith((".localhost", ".local", ".internal")):
        raise UserError("The AI may not open pages on this computer or the local network.", "")
    with contextlib.suppress(ValueError):
        ip = ipaddress.ip_address(host.strip("[]"))
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_unspecified:
            raise UserError("The AI may not open pages on this computer or the local network.", "")
    return str(url).strip()


def _refused_click(text: str) -> str | None:
    low = str(text or "").lower()
    return next((w for w in CLICK_REFUSED if re.search(rf"(?<!\w){re.escape(w)}(?!\w)", low)), None)


def ref_text(page: str | None, ref: str) -> str | None:
    """What the page itself says about the element `ref` (Playwright's snapshot: one line
    `- button "Send" [ref=e9]` and the lines under it, its children), or None when the
    last page read has no such element. Clicking a box clicks what is inside it, so the
    children count too."""
    lines = str(page or "").splitlines()
    for i, line in enumerate(lines):
        if f"[ref={ref}]" in line:
            depth = len(line) - len(line.lstrip())
            out = [line]
            for nxt in lines[i + 1:]:
                if nxt.strip() and len(nxt) - len(nxt.lstrip()) <= depth:
                    break
                out.append(nxt)
            return "\n".join(out)
    return None


def browser_check(name: str, args, page: str | None = None) -> dict:
    """One bridge call as the AI asked for it: the arguments to pass on, or UserError.
    Every rule of the module text, whatever the page or the question says. `page` is
    the last page the AI read (BrowserBridge keeps it): a click is judged by what that
    page says its `ref` is, since the ref is what the bridge clicks, not the AI's words."""
    if name not in BROWSER_ALLOWED:
        raise UserError(f"{name} is not allowed: the AI may only read the browser "
                        "(no typing, keys, forms, scripts, files or uploads).", "")
    args = dict(args or {}) if isinstance(args, dict) or args is None else None
    if args is None:
        raise UserError("The arguments must be an object.", "")
    if any("file" in str(k).lower() or "path" in str(k).lower() for k in args):
        raise UserError("The AI may not save or read files through the browser.", "")
    if name == "browser_navigate":
        args["url"] = _web_url(args.get("url"))
    elif name == "browser_tabs":
        action = str(args.get("action") or "list")
        if action not in TAB_ACTIONS:
            raise UserError(f"Tabs: only {', '.join(TAB_ACTIONS)}.", "")
        if args.get("url"):
            args["url"] = _web_url(args["url"])
    elif name == "browser_click":
        if args.get("doubleClick") or args.get("modifiers") or str(args.get("button") or "left") != "left":
            raise UserError("Only a plain click is allowed.", "")
        if set(args) - {"element", "target", "ref", "button", "doubleClick", "modifiers"}:
            raise UserError("A click takes only element and target.", "")
        if not str(args.get("element") or "").strip():
            raise UserError("Say what is clicked (element), for example 'chat Nhóm A in the chat list'.", "")
        # Playwright MCP 0.0.84 clicks `target`: a snapshot ref (e3, f1e3) or, if it is not
        # one, any CSS or text selector. Only a ref is passed on (a selector could name the
        # Send button without the page's words for it); `ref` is the older field name.
        given = [k for k in ("target", "ref") if args.get(k) is not None]
        ref = args.get(given[0]) if len(given) == 1 else None
        if not isinstance(ref, str) or not REF.fullmatch(ref):
            raise UserError("Say which element is clicked (target) as the page snapshot names it, "
                            "for example e3.", "")
        seen = ref_text(page, ref)
        if seen is None:
            raise UserError(f"{ref} is not on the last page read. Read the page first (browser_snapshot), "
                            "then click something on it.", "")
        word = _refused_click(f"{args['element']}\n{seen}")
        if word:
            raise UserError(f"A click on something that says '{word}' is not allowed: the AI only reads.", "")
    return args


# ------------------------------------------------------------------ connectors.json

class _FileLock(SyncLock):
    name = "connectors.lock"
    wait = 15.0

    def _busy(self) -> UserError:
        return UserError("The connector settings are being saved by another Telegram Assistant.",
                         "Wait a moment and try again.")


def path(home: Path) -> Path:
    return Path(home) / FILE


def load(home: Path) -> dict:
    try:
        data = read_json(path(home), {})
    except UserError:
        data = {}
    data = data if isinstance(data, dict) else {}
    if not isinstance(data.get("browser"), dict):
        data["browser"] = {}
    if not isinstance(data.get("mcp"), dict):
        data["mcp"] = {}
    return data


def update(home: Path, change) -> dict:
    """Change connectors.json under its file lock; returns the new content."""
    with _FileLock(home, "connectors"):
        data = load(home)
        change(data)
        write_json(path(home), data)
        return data


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def browser_on(home: Path | None) -> bool:
    """The user turned on "Let the AI read your browser" (and ticked the warning)."""
    if not home:
        return False
    st = load(Path(home))["browser"]
    return bool(st.get("enabled") and st.get("accepted_at"))


def set_browser(home: Path, enabled: bool, accept: bool = False, token: str | None = None) -> dict:
    """Turn the browser on (only with the warning ticked) or off; keep the bridge's
    optional extension token (it saves the approve page in the browser each time)."""
    def change(d):
        st = d["browser"]
        if accept:
            st["accepted_at"] = _now()
        if enabled and not st.get("accepted_at"):
            raise UserError("Read the warning and tick that you understand it first.", "Then turn it on again.")
        st["enabled"] = bool(enabled)
        if token is not None:
            tok = str(token).strip()
            if tok and not re.fullmatch(r"[A-Za-z0-9_\-.=+/]{8,200}", tok):
                raise UserError("That does not look like the extension's token.",
                                "Copy it again from the Playwright Extension page.")
            st["token"] = tok
    update(home, change)
    return browser_view(home)


def _npm_package(name: str) -> Path | None:
    """The folder of a package installed with `npm install -g` (the app's own installs)."""
    from . import gemini  # noqa: PLC0415  (where npm puts global packages)

    for d in gemini.npm_dirs():
        for root in (d / "node_modules" / name, d.parent / "lib" / "node_modules" / name, d / "lib" / "node_modules" / name):
            if (root / "package.json").exists():
                return root
    return None


def find_bridge() -> list[str] | None:
    """The Playwright MCP bridge as an argv (node + its cli.js, --extension), or None.
    TGKIT_BROWSER_MCP overrides it (tests: a stand-in .py; "off")."""
    override = os.environ.get("TGKIT_BROWSER_MCP")
    if override:
        if override.lower() in ("0", "off", "none"):
            return None
        return [sys.executable, override] if override.endswith(".py") else [override]
    from . import gemini  # noqa: PLC0415

    pkg = _npm_package(BRIDGE_PACKAGE)
    node = gemini.find_node()
    if pkg and node and (pkg / "cli.js").exists():
        return [node, str(pkg / "cli.js"), "--extension"]
    shim = shutil.which("playwright-mcp")
    return [shim, "--extension"] if shim else None


def bridge_version() -> str | None:
    pkg = _npm_package(BRIDGE_PACKAGE)
    try:
        return str(json.loads((pkg / "package.json").read_text(encoding="utf-8")).get("version")) if pkg else None
    except (OSError, ValueError):
        return None


def browser_view(home: Path) -> dict:
    """What Settings shows: the switch, the warning, and per AI what it needs."""
    st = load(home)["browser"]
    bridge = find_bridge()
    return {"enabled": bool(st.get("enabled") and st.get("accepted_at")), "accepted": bool(st.get("accepted_at")),
            "token_set": bool(st.get("token")), "warning": WARNING,
            "claude": {"extension_url": CLAUDE_EXTENSION_URL},
            "bridge": {"installed": bool(bridge), "version": bridge_version(), "package": BRIDGE_PACKAGE,
                       "extension_url": BRIDGE_EXTENSION_URL},
            "allowed": list(BROWSER_ALLOWED), "denied": list(BROWSER_DENIED)}


def bridge_env(home: Path) -> dict:
    env = dict(os.environ)
    tok = load(home)["browser"].get("token")
    if tok:
        env["PLAYWRIGHT_MCP_EXTENSION_TOKEN"] = str(tok)
    return env


# ------------------------------------------------------------------ MCP connectors

# A tool whose name holds one of these words changes something: never allowed.
WRITE_WORDS = frozenset("""add append apply approve archive assign book buy cancel change clear close commit
create delete deploy destroy disable drop edit enable erase exec execute grant insert install invite kill launch
mark merge modify move patch pay post publish purge push put remove rename reply replace reset restart restore
revoke run save schedule send set share start stop submit sync transfer trash unarchive uninstall update upload
upsert write""".split())
URL_HOSTS_HTTP = ("127.0.0.1", "localhost", "::1")
MCP_TIMEOUT = 60
PROTOCOL = "2025-06-18"


def _words(name: str) -> list[str]:
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(name or ""))
    return [w for w in re.split(r"[^A-Za-z0-9]+", spaced.lower()) if w]


def write_reason(tool: dict) -> str | None:
    """Why a tool may change something (then it can never be ticked), or None."""
    ann = tool.get("annotations") if isinstance(tool.get("annotations"), dict) else {}
    if ann.get("destructiveHint") is True:
        return "it says it can delete or change things"
    if ann.get("readOnlyHint") is False:
        return "it says it changes things"
    hit = next((w for w in _words(tool.get("name")) if w in WRITE_WORDS), None)
    if hit:
        return f"its name says it can {hit}"
    return None


def parse_target(target: str) -> dict:
    """A command line ("npx -y some-server D:\\notes") or a URL."""
    raw = str(target or "").strip()
    if not raw:
        raise UserError("Type the command that starts the connector, or its web address.",
                        "For example: npx -y @modelcontextprotocol/server-memory")
    if re.match(r"^[a-z][a-z0-9+.-]*://", raw, re.I):
        u = urlparse(raw)
        if u.scheme == "https" or (u.scheme == "http" and (u.hostname or "") in URL_HOSTS_HTTP):
            return {"url": raw, "command": None}
        raise UserError("A connector's web address must start with https:// (or be on this computer).",
                        "Ask its maker for the https address.")
    words = shlex.split(raw, posix=os.name != "nt")
    words = [w[1:-1] if len(w) > 1 and w[0] == w[-1] and w[0] in "\"'" else w for w in words]
    if not words:
        raise UserError("Type the command that starts the connector.", "")
    return {"url": None, "command": words}


def _resolve(words: list[str]) -> list[str]:
    exe = shutil.which(words[0]) or words[0]
    return [exe, *words[1:]]


class McpClient:
    """A small MCP client: stdio (a command the user typed) or streamable HTTP."""

    def __init__(self, spec: dict, timeout: float = MCP_TIMEOUT):
        self.spec, self.timeout = spec, timeout
        self.proc, self.session, self._q, self._id = None, None, None, 0

    def __enter__(self):
        if self.spec.get("command"):
            try:
                self.proc = subprocess.Popen(_resolve(list(self.spec["command"])), stdin=subprocess.PIPE,
                                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                             env=self.spec.get("env"), creationflags=NO_WINDOW)
            except OSError as exc:
                raise UserError(f"The connector could not start ({exc}).",
                                "Check the command, or install the program it needs.") from None
            self._q = queue.Queue()

            def pump():
                for raw in self.proc.stdout:
                    self._q.put(raw)
                self._q.put(None)
            threading.Thread(target=pump, daemon=True).start()
        self.request("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                    "clientInfo": {"name": "telegram-assistant", "version": __version__}})
        self.notify("notifications/initialized")
        return self

    def __exit__(self, *exc):
        if self.proc is not None:
            with contextlib.suppress(OSError):
                self.proc.stdin.close()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        return False

    def _post(self, msg: dict) -> dict | None:
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                   "MCP-Protocol-Version": PROTOCOL, "User-Agent": f"TelegramAssistant/{__version__}"}
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        req = urllib.request.Request(self.spec["url"], data=json.dumps(msg).encode("utf-8"), headers=headers,
                                     method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310 (https or this computer)
                self.session = resp.headers.get("Mcp-Session-Id") or self.session
                body = resp.read().decode("utf-8", "replace")
                ctype = resp.headers.get("Content-Type") or ""
        except (urllib.error.URLError, OSError) as exc:
            raise UserError(f"The connector did not answer ({exc}).", "Check its address.") from None
        if "id" not in msg:
            return None
        if "text/event-stream" in ctype:
            for line in body.splitlines():
                if line.startswith("data:"):
                    with contextlib.suppress(ValueError):
                        data = json.loads(line[5:].strip())
                        if isinstance(data, dict) and data.get("id") == msg["id"]:
                            return data
            return None
        return json.loads(body) if body.strip() else None

    def _send(self, msg: dict) -> dict | None:
        if self.proc is None:
            return self._post(msg)
        try:
            self.proc.stdin.write((json.dumps(msg) + "\n").encode("utf-8"))
            self.proc.stdin.flush()
        except OSError:
            raise UserError("The connector stopped.", "Check the command in Settings.") from None
        if "id" not in msg:
            return None
        deadline = time.monotonic() + self.timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise UserError("The connector did not answer in time.", "Try again later.")
            try:
                raw = self._q.get(timeout=left)
            except queue.Empty:
                continue
            if raw is None:
                raise UserError("The connector stopped.", "Check the command in Settings.")
            with contextlib.suppress(ValueError):
                data = json.loads(raw.decode("utf-8", "replace"))
                if isinstance(data, dict) and data.get("id") == msg["id"]:
                    return data

    def request(self, method: str, params: dict | None = None):
        self._id += 1
        reply = self._send({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params or {}})
        if not isinstance(reply, dict):
            raise UserError("The connector gave no answer.", "Check the command or the address.")
        if reply.get("error"):
            raise UserError(f"The connector said: {str(reply['error'].get('message'))[:200]}", "")
        return reply.get("result") or {}

    def notify(self, method: str):
        self._send({"jsonrpc": "2.0", "method": method, "params": {}})

    def tools(self) -> list[dict]:
        out, cursor = [], None
        for _ in range(20):
            res = self.request("tools/list", {"cursor": cursor} if cursor else {})
            out += [t for t in res.get("tools") or [] if isinstance(t, dict) and t.get("name")]
            cursor = res.get("nextCursor")
            if not cursor:
                break
        return out

    def call(self, name: str, arguments: dict) -> dict:
        return self.request("tools/call", {"name": name, "arguments": arguments})


def _slug(name: str, taken) -> str:
    base = "-".join(_words(name))[:24] or "connector"
    slug, n = base, 2
    while slug in taken:
        slug, n = f"{base}-{n}", n + 1
    return slug


def _tool_rows(tools: list[dict]) -> list[dict]:
    return [{"name": str(t["name"]), "description": str(t.get("description") or "")[:400],
             "schema": t.get("inputSchema") if isinstance(t.get("inputSchema"), dict) else {"type": "object"},
             "write": write_reason(t)} for t in tools]


def mcp_view(home: Path) -> list[dict]:
    """Every connector, each tool in one of three groups: allowed (ticked), refused
    (looks like it changes something), off (not ticked)."""
    out = []
    for cid, c in load(home)["mcp"].items():
        allowed = set(c.get("allowed") or [])
        tools = [{**t, "state": "refused" if t.get("write") else ("allowed" if t["name"] in allowed else "off"),
                  "write_vi": localize(t.get("write"), "vi")}  # the window shows the reason in its language
                 for t in c.get("tools") or []]
        out.append({"id": cid, "name": c.get("name") or cid, "url": c.get("url"), "command": c.get("command"),
                    "listed_at": c.get("listed_at"), "tools": tools})
    return out


def mcp_add(home: Path, name: str, target: str) -> dict:
    """Start the connector once to list its tools; nothing is ticked yet."""
    spec = parse_target(target)
    name = str(name or "").strip()[:60] or (urlparse(spec["url"]).hostname if spec["url"] else spec["command"][0])
    with McpClient(spec) as cl:
        tools = _tool_rows(cl.tools())

    def change(d):
        cid = _slug(name, d["mcp"])
        d["mcp"][cid] = {"name": name, **spec, "tools": tools, "allowed": [], "listed_at": _now()}
    update(home, change)
    return {"connectors": mcp_view(home)}


def mcp_refresh(home: Path, cid: str) -> dict:
    c = load(home)["mcp"].get(cid)
    if not c:
        raise UserError("That connector is not there any more.", "")
    with McpClient(c) as cl:
        tools = _tool_rows(cl.tools())

    def change(d):
        cur = d["mcp"][cid]
        ok = {t["name"] for t in tools if not t["write"]}
        cur.update(tools=tools, allowed=[t for t in cur.get("allowed") or [] if t in ok], listed_at=_now())
    update(home, change)
    return {"connectors": mcp_view(home)}


def mcp_allow(home: Path, cid: str, names) -> dict:
    """Tick tools. A write-looking tool cannot be ticked: refused with the reason."""
    names = [str(n) for n in (names or [])]

    def change(d):
        c = d["mcp"].get(cid)
        if not c:
            raise UserError("That connector is not there any more.", "")
        by = {t["name"]: t for t in c.get("tools") or []}
        for n in names:
            if n not in by:
                raise UserError(f"The connector has no tool named {n}.", "Press Check tools again.")
            if by[n].get("write"):
                raise UserError(f"{n} cannot be allowed: {by[n]['write']}.",
                                "Telegram Assistant lets the AI only read.")
        c["allowed"] = names
    update(home, change)
    return {"connectors": mcp_view(home)}


def mcp_remove(home: Path, cid: str) -> dict:
    update(home, lambda d: d["mcp"].pop(cid, None))
    return {"connectors": mcp_view(home)}


def ai_tools(home: Path) -> list[dict]:
    """What the AI sees (connector_tools): only allowed tools, as <connector>/<tool>."""
    out = []
    for c in mcp_view(home):
        for t in c["tools"]:
            if t["state"] == "allowed":
                out.append({"name": f"{c['id']}/{t['name']}", "connector": c["name"],
                            "description": t["description"], "fields": t["schema"]})
    return out


def ai_call(home: Path, name: str, arguments: str | None) -> str:
    """connector_call: run one allowed tool after checking it again on the live list."""
    cid, _, tool = str(name or "").partition("/")
    c = load(home)["mcp"].get(cid)
    if not c or tool not in (c.get("allowed") or []):
        raise UserError(f"{name} is not a tool the user allowed.", "Use connector_tools to see the allowed ones.")
    try:
        args = json.loads(arguments) if arguments else {}
    except ValueError:
        raise UserError("arguments must be JSON text, e.g. {\"query\": \"x\"}.", "") from None
    if not isinstance(args, dict):
        raise UserError("arguments must be a JSON object.", "")
    with McpClient(c) as cl:
        live = {t["name"]: t for t in cl.tools()}
        if tool not in live:
            raise UserError(f"The connector no longer has {tool}.", "")
        why = write_reason(live[tool])
        if why:
            raise UserError(f"{tool} is refused now: {why}.", "Telegram Assistant lets the AI only read.")
        res = cl.call(tool, args)
    text = "\n".join(str(x.get("text") or "") for x in res.get("content") or [] if isinstance(x, dict))
    if res.get("isError"):
        raise UserError(f"The connector said: {text[:500]}", "")
    return text or json.dumps(res.get("structuredContent") or {}, ensure_ascii=False)


class BrowserBridge:
    """The Playwright MCP bridge for one answer, started by tgkit's MCP server when the
    AI's CLI lists the tools (tools/list, at the start of every answer while the browser
    is on) and kept until the answer ends. Every call goes through browser_check first,
    with the last page the AI read (self.page) to judge a click by; the AI never talks
    to the bridge itself."""

    def __init__(self, home: Path):
        self.home = Path(home)
        self.client = None
        self._tools = None
        self.page = None  # the last snapshot the bridge returned (its refs are what a click names)

    def tools(self) -> list[dict]:
        """The allowed bridge tools with the bridge's own schemas (no filename field)."""
        if self._tools is None:
            argv = find_bridge()
            if not argv:
                self._tools = []
                return self._tools
            try:
                listed = self._client().tools()
            except UserError:
                self._tools = []
                return self._tools
            out = []
            third = browser_buckets(t.get("name") for t in listed)["unknown"]
            if third:  # a newer bridge's tool in neither list: denied, and said so
                print(f"tgkit: browser tools not passed on (not on the read-only list): {', '.join(third)}",
                      file=sys.stderr, flush=True)
            for t in listed:
                if t.get("name") in BROWSER_ALLOWED:
                    schema = json.loads(json.dumps(t.get("inputSchema") or {"type": "object"}))
                    props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
                    for k in [k for k in props if "file" in k.lower() or "path" in k.lower()]:
                        props.pop(k)
                    if isinstance(schema.get("required"), list):
                        schema["required"] = [k for k in schema["required"] if k in props]
                    out.append({"name": t["name"], "description": str(t.get("description") or "")[:600],
                                "inputSchema": schema,
                                "annotations": {"readOnlyHint": True, "destructiveHint": False}})
            self._tools = out
        return self._tools

    def _client(self):
        if self.client is None:
            argv = find_bridge()
            if not argv:
                raise UserError("The browser helper is not installed.", "Settings, Browser: press Install.")
            self.client = McpClient({"command": argv, "env": bridge_env(self.home)}, timeout=120).__enter__()
        return self.client

    def call(self, name: str, args) -> dict:
        checked = browser_check(name, args, self.page)
        res = self._client().call(name, checked)
        text = "\n".join(str(c.get("text") or "") for c in res.get("content") or []
                         if isinstance(c, dict) and c.get("type") == "text")
        if "[ref=" in text and name not in KEEPS_PAGE:
            self.page = text
        elif name not in KEEPS_PAGE:  # the page changed and no snapshot came back: old refs are void
            self.page = None
        return res

    def close(self):
        if self.client is not None:
            with contextlib.suppress(Exception):
                self.client.__exit__(None, None, None)
            self.client = None
