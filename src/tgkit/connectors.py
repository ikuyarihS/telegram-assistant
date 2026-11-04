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
import socket
import subprocess
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, unquote, unquote_plus, urljoin, urlparse, urlsplit

from . import __version__
from .errors import UserError, localize
from .store import SyncLock, read_json, write_json

FILE = "connectors.json"
NO_WINDOW = 0x08000000 if os.name == "nt" else 0

# ------------------------------------------------------------------ the browser

WARNING = ("When this is on, the AI can read the web pages open in your own Chrome or Edge, including sites you "
           "are signed in to, such as WhatsApp Web or Zalo. It can open the sites on your list (WhatsApp Web and "
           "Zalo unless you add more), read them and click to open a chat; it cannot type, press Send, open a "
           "link that sends or shares, post or change settings. WhatsApp and Zalo do not officially allow other "
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
SEND_QUERY = frozenset(("text", "body", "message", "msg", "content", "caption", "status", "tweet", "share",
                        "phone"))
REF = re.compile(r"(?:f\d{1,6})?e\d{1,9}")  # Playwright's own test for a ref: /^(f\d+)?e\d+$/
KEEPS_PAGE = ("browser_take_screenshot", "browser_find")  # these do not change the page the refs belong to

# The sites the AI may open (the owner's ruling for 0.3.0: "Chat sites only"): WhatsApp Web
# and Zalo Web (chat.zalo.me serves the web app itself; checked 2026-11-04). The user adds
# or removes sites in Settings; a site covers its own subdomains. Every way to a page
# goes through url_check: open (navigate, a new tab), a click on a link or on anything
# inside or around one, and Claude in Chrome's navigate (chrome_hook). This computer and
# the local network are never allowed, whatever the list says, in any spelling.
DEFAULT_SITES = ("web.whatsapp.com", "chat.zalo.me")
MAX_SITES = 50
HOST = re.compile(r"(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?")
LOCAL_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home", ".home.arpa", ".intranet", ".corp")
TEST_DNS = "TGKIT_TEST_DNS"  # tests only: {"host": ["1.2.3.4"]} instead of asking DNS (no network in tests)
LOCAL = "The AI may not open pages on this computer or the local network."
NOT_PLAIN = "The AI may only open plain web addresses (http or https, a site name, no user name or odd signs)."


def _ipv4(host: str):
    """The host as Chrome reads it when it is a number (the WHATWG URL rules): 127.1,
    2130706433, 0x7f000001 and 0177.0.0.1 are all 127.0.0.1. None for a name."""
    parts = host.split(".")
    if len(parts) > 1 and parts[-1] == "":
        parts = parts[:-1]
    if not re.fullmatch(r"[0-9]+|0x[0-9a-f]*", parts[-1]):
        return None
    if len(parts) > 4:
        raise UserError(NOT_PLAIN, "")
    nums = []
    for p in parts:
        if re.fullmatch(r"0x[0-9a-f]*", p):
            nums.append(int(p[2:] or "0", 16))
        elif len(p) > 1 and p.startswith("0"):
            if not re.fullmatch(r"[0-7]+", p):
                raise UserError(NOT_PLAIN, "")
            nums.append(int(p, 8))
        elif re.fullmatch(r"[0-9]+", p):
            nums.append(int(p))
        else:
            raise UserError(NOT_PLAIN, "")
    if any(n > 255 for n in nums[:-1]) or nums[-1] >= 256 ** (5 - len(nums)):
        raise UserError(NOT_PLAIN, "")
    value = nums[-1]
    for i, n in enumerate(nums[:-1]):
        value += n * 256 ** (3 - i)
    return ipaddress.IPv4Address(value)


def _public(ip) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return bool(ip.is_global) and not ip.is_multicast


def _addresses(host: str) -> list:
    """What the host's name resolves to (every address), [] when it does not resolve."""
    fake = os.environ.get(TEST_DNS)
    if fake:
        try:
            found = (json.loads(fake) or {}).get(host) or []
        except (ValueError, AttributeError):
            found = []
    else:
        try:
            found = [i[4][0] for i in socket.getaddrinfo(host, None)]
        except (OSError, UnicodeError, ValueError):
            found = []
    out = []
    for a in found:
        with contextlib.suppress(ValueError):
            out.append(ipaddress.ip_address(str(a).split("%")[0]))
    return out


# A value that names a host, with or without slashes: evil.com/?q=chat, //evil.com,
# \\evil.com, user@evil.com, evil.com:8080, 10.0.0.1, 127.1, 0x7f000001, [::1], localhost:8765.
_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*:")
_HOSTLIKE = re.compile(r"^[/\\]*(?:([^/\\?#@\s]*)@)?(\[[0-9a-f:.]+\]|[\w-]+(?:\.[\w-]+)*)\.?"
                       r"(:\d*)?(?:[/\\?#]|$)")


def _names_a_host(token: str, sites=()) -> bool:
    """Does this value read as a host off the list? Read with and without a leading
    scheme (localhost:8765 is a host and a port, http:evil.com a scheme and a host)."""
    for text in {token, _SCHEME.sub("", token)}:
        m = _HOSTLIKE.match(text)
        if not m:
            continue
        user, host, port = m.groups()
        if host.startswith("["):
            return True
        if host == "localhost" or host.endswith(".localhost"):
            return True
        try:
            ip = _ipv4(host)
        except UserError:  # a number Chrome does not read as an address (2.3000.1): not a host
            continue
        if ip is not None:
            # 127.1, 0177.0.0.1, 0x7f.1, 0x7f000001, 2130706433 are all hosts; a short bare
            # number (?page=2, a time 12:30) stays a number, as Chrome cannot reach 0.0.0.x.
            if "." in host or host.startswith("0x") or int(ip) >= 256 ** 3:
                return True
            continue
        if "." not in host:  # one word: a host only with a user name or a port (router:8080)
            if (user is not None or port) and re.search(r"[^\W\d_]", host):
                return True
            continue
        last = host.rsplit(".", 1)[-1]
        if re.fullmatch(r"[^\W\d_]{2,}|xn--[a-z0-9-]+", last) and not site_of(host, sites):
            return True
    return False


def _carries_link(u, sites=()) -> bool:
    """A query or fragment holding another address (an open redirect to another site):
    a URL, or any value that reads as a host not on the user's list (gate 2 NOTE 3)."""
    # Decoded as a server reads a form value ("+" is a space, as parse_qs has it), twice.
    text = unicodedata.normalize("NFKC", unquote_plus(unquote_plus(f"{u.query}#{u.fragment}"))).lower()
    for dot in ("。", "．", "｡"):  # the dots IDNA reads as "."
        text = text.replace(dot, ".")
    if "://" in text or re.search(r"(?:^|[=&#?;,|+\s\"'()<>])(?:https?:)?//", text):
        return True
    return any(_names_a_host(token, sites) for token in re.split(r"[&;=?#,|+\s\"'()<>]+", text) if token)


def url_check(url, sites=None, send: bool = True, dns: bool = True) -> str:
    """The address as given when the AI may open it, else UserError: http(s), a plain
    site name on the list (or under one), not a link that sends, shares or passes on
    another address, and a name that resolves only to public addresses. Never this
    computer or the local network: localhost, 127.1, 2130706433, 0x7f000001, [::1],
    ::ffff:127.0.0.1, a .local name, or a name that resolves to one of them."""
    sites = list(DEFAULT_SITES) if sites is None else list(sites)
    text = str(url or "").strip()
    if not text or re.search(r"[\x00-\x20\x7f\\]", text):
        raise UserError(NOT_PLAIN, "")
    u = urlsplit(text)
    if u.scheme.lower() not in ("http", "https") or not u.netloc:
        raise UserError("The AI may only open web pages (http or https).", "")
    if "@" in u.netloc or "%" in u.netloc or not u.netloc.isascii():
        raise UserError(NOT_PLAIN, "")
    try:
        port = u.port
    except ValueError:
        raise UserError(NOT_PLAIN, "") from None
    host = (u.hostname or "").lower().rstrip(".")
    if not host:
        raise UserError(NOT_PLAIN, "")
    if ":" in host:
        try:
            ip = ipaddress.ip_address(host.split("%")[0])
        except ValueError:
            raise UserError(NOT_PLAIN, "") from None
    else:
        ip = _ipv4(host)
    if ip is not None:
        if not _public(ip):
            raise UserError(LOCAL, "")
        raise UserError(f"The AI opens only the sites on the user's list, and {host} is not on it.", "")
    if host == "localhost" or host.endswith(LOCAL_SUFFIXES) or not HOST.fullmatch(host):
        raise UserError(LOCAL, "")
    if not site_of(host, sites):
        raise UserError(f"The AI opens only the sites on the user's list, and {host} is not on it.",
                        "The user can add a site in Settings, Your browser.")
    if port not in (None, 80, 443):
        raise UserError(NOT_PLAIN, "")
    if send and (SEND_PATH.search(u.path or "") or _carries_link(u, sites)
                 or any(k.lower() in SEND_QUERY for k in parse_qs(u.query, True))):
        raise UserError("The AI may not open a link that writes, sends or shares a message.", "")
    if dns:
        found = _addresses(host)
        if not found:
            raise UserError(f"{host} could not be found.", "")
        if not all(_public(a) for a in found):
            raise UserError(LOCAL, "")
    return text


def site_of(host: str, sites) -> str | None:
    """The list entry that covers this host (the site itself or a subdomain of it)."""
    host = str(host or "").lower().rstrip(".")
    return next((s for s in sites if host == s or host.endswith("." + s)), None)


def _sites_of(st: dict) -> list[str]:
    raw = st.get("sites")
    if not isinstance(raw, list):
        return list(DEFAULT_SITES)
    out = []
    for s in (str(x).strip().lower() for x in raw):
        if HOST.fullmatch(s) and not s.endswith(LOCAL_SUFFIXES) and s not in out:
            out.append(s)
    return out


def sites(home) -> list[str]:
    """The sites the AI may open (Settings); WhatsApp Web and Zalo until the user changes it."""
    return _sites_of(load(Path(home))["browser"]) if home else list(DEFAULT_SITES)


def normal_site(text) -> str:
    """What the user typed as a site (chat.zalo.me, https://chat.zalo.me/...) as a site name."""
    t = str(text or "").strip().lower()
    try:
        t = (urlsplit(t).hostname or "") if "://" in t else re.split(r"[/?#]", t, maxsplit=1)[0]
    except ValueError:
        t = ""
    t = t.rstrip(".")
    if t.startswith("*."):
        t = t[2:]
    number = ":" in t
    if not number:
        try:
            number = _ipv4(t) is not None if t else False
        except UserError:
            number = True
    if number or t == "localhost" or t.endswith(LOCAL_SUFFIXES) or not HOST.fullmatch(t):
        raise UserError("That is not a web site the AI may open.",
                        "Type the site's name, for example chat.zalo.me. This computer and your home network are never allowed.")
    return t


def set_sites(home: Path, add=None, remove=None) -> dict:
    """Add or remove one site of the list (Settings, Your browser)."""
    new = normal_site(add) if add is not None else None

    def change(d):
        st = d["browser"]
        cur = _sites_of(st)
        if new and new not in cur:
            if len(cur) >= MAX_SITES:
                raise UserError("The list is full.", "Remove a site you no longer need first.")
            cur.append(new)
        if remove is not None:
            gone = str(remove).strip().lower()
            cur = [s for s in cur if s != gone]
        st["sites"] = cur
    update(home, change)
    return browser_view(home)


def _refused_click(text: str) -> str | None:
    low = unicodedata.normalize("NFC", str(text or "")).lower()  # "Gửi" typed as u + two accents too
    return next((w for w in CLICK_REFUSED if re.search(rf"(?<!\w){re.escape(w)}(?!\w)", low)), None)


# ------------------------------------------------------------------ the page's own tree

# Roles whose name is what a person sees on the thing they press (a button, a link, a
# menu entry, an icon). A click on, in or around one named Send / Gửi / ... is refused.
NAMED_ROLES = frozenset(("button", "link", "menuitem", "menuitemcheckbox", "menuitemradio", "checkbox", "switch",
                         "radio", "tab", "option", "img", "image", "treeitem", "menu", "menubar", "toolbar"))
TYPE_ROLES = frozenset(("textbox", "searchbox", "combobox", "spinbutton", "slider"))  # a box to type in
# Pressed, not opened: with no word on it (no name, or only an icon such as "➤") the AI cannot
# tell Send from anything else, so it is refused (gate 2 NOTE 2). So is any unnamed thing in
# the message composer: the area around the box where a message is typed. A list (the chat
# list) between the target and that box means it is not in the composer.
BUTTON_ROLES = frozenset(("button", "menuitem", "menuitemcheckbox", "menuitemradio", "switch", "checkbox", "radio",
                          "tab", "option", "combobox", "slider", "spinbutton"))
LIST_ROLES = frozenset(("list", "listbox", "grid", "treegrid", "table", "tree", "rowgroup", "feed"))
SEARCH_BOX = re.compile(r"search|tìm|find", re.I)
_ROLE = re.compile(r"[a-z][a-zA-Z0-9-]*")
_ATTR = re.compile(r" \[([a-z][a-z-]*)(?:=([^\]\s]*))?\]")


class Node:
    """One element of the page as Playwright's snapshot lists it: its role, name, own text
    (after the colon on its own line), ref, link and whether it shows as clickable."""
    __slots__ = ("role", "name", "text", "ref", "url", "pointer", "parent", "children")

    def __init__(self, role, name, ref, parent, text="", pointer=False):
        self.role, self.name, self.text, self.ref = role, name, text, ref
        self.url, self.pointer, self.parent, self.children = None, pointer, parent, []

    def words(self) -> str:
        """What a person reads on the element itself: its name and its own text."""
        return f"{self.name} {self.text}"


def _quoted(s: str, i: int):
    """A double-quoted string starting at s[i] (backslash escapes): (text, index after it)."""
    out, j = [], i + 1
    while j < len(s):
        c = s[j]
        if c == "\\" and j + 1 < len(s):
            out.append(s[j + 1])
            j += 2
            continue
        if c == '"':
            return "".join(out), j + 1
        out.append(c)
        j += 1
    return None


def _scalar(v: str) -> str:
    """A YAML value, unquoted when it is quoted."""
    v = v.strip()
    if v[:1] == '"':
        q = _quoted(v, 0)
        return q[0] if q else v
    if v[:1] == "'" and v.endswith("'") and len(v) > 1:
        return v[1:-1].replace("''", "'")
    return v


def _node_line(body: str):
    """`role "name" [attr] [ref=e3]` (then nothing, `:` or `: text`): (role, name, attrs),
    or None when the line is not in that shape. Only these parts can give a ref: the
    text after the colon and the inside of the name are never searched."""
    if body[:1] in ("'", '"'):  # the whole key quoted by YAML: `- 'link "a: b" [ref=e5]':`
        if body[0] == "'":
            out, j = [], 1
            while j < len(body):
                if body[j] == "'":
                    if body[j + 1:j + 2] == "'":
                        out.append("'")
                        j += 2
                        continue
                    break
                out.append(body[j])
                j += 1
            else:
                return None
            key, after = "".join(out), body[j + 1:]
        else:
            q = _quoted(body, 0)
            if not q:
                return None
            key, after = q[0], body[q[1]:]
        if not (after == "" or after == ":" or after.startswith(": ")):
            return None
        parsed = _node_line(key)
        return (parsed[0], parsed[1], parsed[2], after) if parsed and parsed[3] == "" else None
    m = _ROLE.match(body)
    if not m:
        return None
    role, i, name = m.group(0), m.end(), ""
    if body[i:i + 2] == ' "':
        q = _quoted(body, i + 1)
        if not q:
            return None
        name, i = q
    attrs = {}
    while True:
        a = _ATTR.match(body, i)
        if not a:
            break
        attrs[a.group(1)] = a.group(2) or ""
        i = a.end()
    rest = body[i:]
    if not (rest == "" or rest == ":" or rest.startswith(": ")):
        return None
    return role, name, attrs, rest


def snapshot_tree(page: str | None) -> dict | None:
    """The page snapshot as a tree: {"url": the page's address or None, "refs": {ref: Node}},
    or None when the text holds no snapshot. A ref belongs to the element whose own
    line carries it as its [ref=...] attribute; a ref two elements claim is None (never
    clicked). Message text on the page cannot add, move or hide a ref."""
    lines = str(page or "").splitlines()
    page_url, start, fenced = None, None, False
    for i, line in enumerate(lines):
        s = line.strip()
        if page_url is None and s.startswith("- Page URL:"):
            page_url = s[len("- Page URL:"):].strip() or None
        if s.startswith("```yaml"):
            start, fenced = i + 1, True
            break
        if s == "- Page Snapshot:":
            start = i + 1
            break
    if start is None:
        return None
    nodes, stack = [], []
    for line in lines[start:]:
        if fenced and line.strip().startswith("```"):
            break
        body = line.lstrip(" ")
        if not body.startswith("- "):
            continue
        indent = len(line) - len(body)
        body = body[2:].rstrip()
        while stack and stack[-1][0] >= indent:
            stack.pop()
        parent = stack[-1][1] if stack else None
        prop = re.match(r"/([A-Za-z]+):\s*(.*)$", body)
        if prop:  # `- /url: https://...`: a property of the element above
            if parent is not None and prop.group(1) == "url" and parent.url is None:
                parent.url = _scalar(prop.group(2))
            continue
        parsed = _node_line(body)
        if parsed:
            role, name, attrs, rest = parsed
            node = Node(role, name, attrs.get("ref"), parent, _scalar(rest[2:]) if rest.startswith(": ") else "",
                        attrs.get("cursor") == "pointer")
        else:
            node = Node(None, "", None, parent)
        if parent is not None:
            parent.children.append(node)
        nodes.append(node)
        stack.append((indent, node))
    refs: dict = {}
    for n in nodes:
        if n.ref:
            refs[n.ref] = None if n.ref in refs else n
    return {"url": page_url, "refs": refs}


def _click_check(ref: str, element: str, page: str | None, allowed_sites) -> None:
    """A click on `ref` of the last page read: refused (UserError) when the ref is not one
    element of that page, or when it, anything around it or anything inside it is a box
    to type in, something named Send / Gửi / Delete / ..., or a link to a page the AI may
    not open (off the list, this computer, or one that sends or shares)."""
    tree = snapshot_tree(page)
    if tree is None or ref not in tree["refs"]:
        raise UserError(f"{ref} is not on the last page read. Read the page first (browser_snapshot), "
                        "then click something on it.", "")
    node = tree["refs"][ref]
    if node is None:
        raise UserError(f"{ref} is not one clear element of the page, so it is not clicked.", "")
    word = _refused_click(element)
    if word:
        raise UserError(f"A click on something that says '{word}' is not allowed: the AI only reads.", "")
    around, n = [], node
    while n is not None:
        around.append(n)
        n = n.parent
    inside, todo = [], list(node.children)
    while todo:
        n = todo.pop()
        inside.append(n)
        todo.extend(n.children)
    for n in around + inside:
        if n.role is None:
            raise UserError("That part of the page could not be read clearly, so it is not clicked.", "")
        if n.role in TYPE_ROLES and n in around:
            raise UserError("That is a box to type in: the AI does not type.", "")
        # The element and everything inside it: its name and own text, whatever its role
        # (a div saying "Gửi", a button holding `- text: Send`). Around it: only what is
        # itself pressable (a named role or shown clickable), never a chat row's preview.
        judged = n not in around[1:] or n.role in NAMED_ROLES or n.pointer
        word = _refused_click(n.words()) if judged else None
        if word:
            raise UserError(f"A click on something that says '{word}' is not allowed: the AI only reads.", "")
        if n.url is not None:
            url_check(urljoin(tree["url"] or "", n.url), allowed_sites)
    unnamed = not any(ch.isalnum() for x in [node, *inside] for ch in unicodedata.normalize("NFKC", x.words()))
    if unnamed and (node.role in BUTTON_ROLES or _in_composer(node)):
        raise UserError("That has no words on it (only an icon, or nothing), and it is a button or sits by the "
                        "box where a message is typed, so the AI does not press it.", "")


def _in_composer(node) -> bool:
    """Whether the element sits in the message composer: going up from it, the first
    element that holds a box to type in holds one that is not a search box, with no
    list (the chat list) on the way."""
    n = node.parent
    while n is not None:
        if n.role in LIST_ROLES:
            return False
        boxes, todo = [], list(n.children)
        while todo:
            d = todo.pop()
            if d.role in ("textbox", "searchbox"):
                boxes.append(d)
            todo.extend(d.children)
        if boxes:
            return any(b.role == "textbox" and not SEARCH_BOX.search(b.name) for b in boxes)
        n = n.parent
    return False


def browser_check(name: str, args, page: str | None = None, home=None) -> dict:
    """One bridge call as the AI asked for it: the arguments to pass on, or UserError.
    Every rule of the module text, whatever the page or the question says. `page` is
    the last page the AI read (BrowserBridge keeps it): a click is judged by what that
    page's own tree says its `ref` is, since the ref is what the bridge clicks, not the
    AI's words. `home` gives the user's list of sites (the defaults without it)."""
    allowed_sites = sites(home)
    if name not in BROWSER_ALLOWED:
        raise UserError(f"{name} is not allowed: the AI may only read the browser "
                        "(no typing, keys, forms, scripts, files or uploads).", "")
    args = dict(args or {}) if isinstance(args, dict) or args is None else None
    if args is None:
        raise UserError("The arguments must be an object.", "")
    if any("file" in str(k).lower() or "path" in str(k).lower() for k in args):
        raise UserError("The AI may not save or read files through the browser.", "")
    if name == "browser_navigate":
        args["url"] = url_check(args.get("url"), allowed_sites)
    elif name == "browser_tabs":
        action = str(args.get("action") or "list")
        if action not in TAB_ACTIONS:
            raise UserError(f"Tabs: only {', '.join(TAB_ACTIONS)}.", "")
        if args.get("url"):
            args["url"] = url_check(args["url"], allowed_sites)
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
        _click_check(ref, str(args["element"]), page, allowed_sites)
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
    """connectors.json; a damaged file reads as empty (the browser off, no connector) and
    is marked "_damaged", so update() keeps a copy of it before anything is written."""
    damaged = False
    try:
        data = read_json(path(home), {})
    except UserError:
        data, damaged = {}, True
    if not isinstance(data, dict):
        data, damaged = {}, True
    if not isinstance(data.get("browser"), dict):
        data["browser"] = {}
    if not isinstance(data.get("mcp"), dict):
        data["mcp"] = {}
    if damaged:
        data["_damaged"] = True
    return data


def _keep_damaged(home: Path) -> str:
    """Copy a damaged connectors.json aside (never lost); the copy's name, or UserError."""
    src = path(home)
    dest = src.with_name(f"connectors.damaged-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json")
    n = 1
    while dest.exists():
        n += 1
        dest = src.with_name(f"connectors.damaged-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{n}.json")
    try:
        shutil.copy2(src, dest)
    except OSError:
        raise UserError("The connector settings file is damaged and could not be copied aside, so nothing was changed.",
                        "Close other programs that may use the data folder and try again.") from None
    return dest.name


def update(home: Path, change) -> dict:
    """Change connectors.json under its file lock; returns the new content. A damaged file
    is first copied aside (connectors.damaged-<time>.json) and Settings says so."""
    with _FileLock(home, "connectors"):
        data = load(home)
        if data.pop("_damaged", False) and path(home).exists():
            data["damaged_copy"] = _keep_damaged(home)
        change(data)
        write_json(path(home), data)
        return data


def damaged_view(home: Path) -> dict | None:
    """For Settings: the file is damaged now ("now"), or was and a copy was kept ("copy")."""
    data = load(home)
    if data.get("_damaged"):
        return {"state": "now", "copy": None}
    if data.get("damaged_copy"):
        return {"state": "copy", "copy": str(data["damaged_copy"])}
    return None


# ------------------------------------------------------------------ is the extension connected

STATUS_FILE = ("app", "browser-status.json")
PROVIDERS = ("claude", "gemini", "gpt")
NOT_CONNECTED = re.compile(r"not connected|no (?:chrome|browser|extension)|extension (?:is )?not|"
                           r"connect(?:ion)? (?:to|with) (?:the )?(?:extension|browser|chrome) failed|"
                           r"install (?:the )?extension|is the extension", re.I)


def record_status(home, who: str, state: str) -> None:
    """What the last browser use of one AI showed: "connected" or "not_connected"."""
    if not home or who not in PROVIDERS or state not in ("connected", "not_connected"):
        return
    f = Path(home).joinpath(*STATUS_FILE)
    with contextlib.suppress(OSError, UserError):
        cur = read_json(f, {})
        cur = cur if isinstance(cur, dict) else {}
        if (cur.get(who) or {}).get("state") == state and state == "connected" and \
                (cur.get(who) or {}).get("at", "")[:13] == _now()[:13]:
            return  # already said this hour
        cur[who] = {"state": state, "at": _now()}
        f.parent.mkdir(parents=True, exist_ok=True)
        write_json(f, cur)


def status_view(home) -> dict:
    """Per AI: connected / not_connected (from its last use of the browser) or not_checked.
    Nothing is opened to check it: the extension shows itself only when the AI uses it."""
    try:
        cur = read_json(Path(home).joinpath(*STATUS_FILE), {})
    except UserError:
        cur = {}
    cur = cur if isinstance(cur, dict) else {}
    out = {}
    for p in PROVIDERS:
        st = cur.get(p) if isinstance(cur.get(p), dict) else {}
        out[p] = {"state": st.get("state") if st.get("state") in ("connected", "not_connected") else "not_checked",
                  "at": str(st.get("at") or "") or None}
    return out


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
    status = status_view(home)
    if st.get("enabled") and st.get("accepted_at"):
        from . import claude  # noqa: PLC0415

        if claude.hook_problem(home):  # Claude's address hook cannot run here: Claude gets no browser
            status["claude"] = {"state": "hook_failed", "at": None}
    return {"enabled": bool(st.get("enabled") and st.get("accepted_at")), "accepted": bool(st.get("accepted_at")),
            "token_set": bool(st.get("token")), "warning": WARNING,
            "sites": _sites_of(st), "default_sites": list(DEFAULT_SITES),
            "status": status, "damaged": damaged_view(home),
            "claude": {"extension_url": CLAUDE_EXTENSION_URL},
            "bridge": {"installed": bool(bridge), "version": bridge_version(), "package": BRIDGE_PACKAGE,
                       "extension_url": BRIDGE_EXTENSION_URL},
            "allowed": list(BROWSER_ALLOWED), "denied": list(BROWSER_DENIED)}


HOOK_ONLY = "Telegram Assistant allows only Claude in Chrome's navigate here."  # claude.hook_problem asks for it


def chrome_hook(home, stdin=None, stdout=None, stderr=None) -> int:
    """Claude Code's PreToolUse hook for Claude in Chrome's navigate and tabs_create_mcp
    (claude.py passes it per run with --settings). Those tools are not in --allowedTools,
    so with --permission-mode dontAsk they run only when this says "allow": an address
    the AI may open (url_check, the user's list), with the browser still on. Anything
    else, a bad input or any error: exit 2, which refuses the call."""
    stdin, stdout, stderr = stdin or sys.stdin, stdout or sys.stdout, stderr or sys.stderr
    try:
        data = json.loads(stdin.read() or "{}")
        tool = str(data.get("tool_name") or "")
        given = data.get("tool_input") if isinstance(data.get("tool_input"), dict) else None
        if given is None or not tool.startswith("mcp__claude-in-chrome__"):
            raise UserError(HOOK_ONLY, "")
        short = tool[len("mcp__claude-in-chrome__"):]
        if short not in ("navigate", "tabs_create_mcp"):
            raise UserError(f"{short} is not allowed: the AI may only read the browser.", "")
        if not browser_on(home):
            raise UserError("The browser is off. The user turns it on in Settings.", "")
        own = sites(home)
        urls = [v for k, v in given.items()
                if str(k).lower() in ("url", "href", "uri", "link") or (isinstance(v, str) and "//" in v)]
        if short == "navigate" and not urls:
            raise UserError("Give the page's web address (url).", "")
        for v in urls:
            url_check(v, own)
    except UserError as exc:
        print(exc.message, file=stderr, flush=True)
        return 2
    except Exception as exc:  # noqa: BLE001  (refuse on anything unexpected)
        print(f"Refused: {type(exc).__name__}.", file=stderr, flush=True)
        return 2
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow",
                                             "permissionDecisionReason": "a page on the user's list"}}),
          file=stdout, flush=True)
    return 0


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
        self.url = None  # the address of that page, when it is on the user's list

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
        checked = browser_check(name, args, self.page, self.home)
        if name in KEEPS_PAGE and not self.url:
            raise UserError("Read the page first (browser_snapshot): the AI reads only pages on the user's list.", "")
        who = os.environ.get("TGKIT_BROWSER_FOR") or ""
        try:
            res = self._client().call(name, checked)
        except UserError:
            record_status(self.home, who, "not_connected")
            raise
        text = "\n".join(str(c.get("text") or "") for c in res.get("content") or []
                         if isinstance(c, dict) and c.get("type") == "text")
        if res.get("isError"):
            if NOT_CONNECTED.search(text):
                record_status(self.home, who, "not_connected")
        else:
            record_status(self.home, who, "connected")
        # Where the browser is now: a page off the list (a link the page itself followed,
        # going back, another tab) is not passed on, and its refs are not clickable.
        m = re.search(r"^\s*- Page URL:\s*(\S+)", text.split("```yaml")[0].split("- Page Snapshot:")[0], re.M)
        if m:
            try:
                url_check(m.group(1), sites(self.home), send=False)
                self.url = m.group(1)
            except UserError as exc:
                self.page, self.url = None, None
                return {"content": [{"type": "text", "text": "The browser is now on a page that is not on the user's "
                                     f"list of sites, so Telegram Assistant does not pass it on ({exc.message}). "
                                     "Open a page on the list with browser_navigate."}], "isError": True}
        elif name not in KEEPS_PAGE and not (name == "browser_tabs" and checked.get("action", "list") == "list"):
            self.url = None  # another page or tab now, and no address came back
        if name == "browser_tabs":
            res = {**res, "content": [self._tabs_only_listed(c) for c in res.get("content") or []]}
        if "[ref=" in text and name not in KEEPS_PAGE:
            self.page = text if m else None
        elif name not in KEEPS_PAGE:  # the page changed and no snapshot came back: old refs are void
            self.page = None
        return res

    def _tabs_only_listed(self, c):
        """The tab list without the titles and addresses of tabs off the user's list."""
        if not isinstance(c, dict) or c.get("type") != "text":
            return c
        own, out = sites(self.home), []
        for line in str(c.get("text") or "").splitlines():
            m = re.match(r"^(\s*-\s*\d+:\s*(?:\(current\)\s*)?)\[(.*)\]\s*\((\S*)\)\s*$", line)
            if m:
                try:
                    url_check(m.group(3), own, send=False, dns=False)
                    out.append(line)
                except UserError:
                    out.append(f"{m.group(1)}[a page that is not on the user's list]")
            elif re.match(r"^\s*-\s*\d+:", line):
                out.append(re.match(r"^(\s*-\s*\d+:)", line).group(1) + " [a page that is not on the user's list]")
            else:
                out.append(line)
        return {**c, "text": "\n".join(out)}

    def close(self):
        if self.client is not None:
            with contextlib.suppress(Exception):
                self.client.__exit__(None, None, None)
            self.client = None
