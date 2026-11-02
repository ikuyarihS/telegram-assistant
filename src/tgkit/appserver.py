"""The TG Kit app's local server: the web UI plus a small JSON API on 127.0.0.1.

Only this computer can reach it (it binds 127.0.0.1), and every /api call
must carry the random token written into the page, so another web page open
in a browser cannot call it. Answers stream as NDJSON (one JSON per line).

It reads through engine (the same code the CLI runs), writes only
tasks.json (task buttons) and the app's own conversation files, and gets new
messages by running `tgkit sync` itself, so the sync lock and every rule in
docs/MAINTENANCE.md apply unchanged.
"""

import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import __version__, config, engine, intent
from .claude import ClaudeRunner
from .conversations import Conversations
from .errors import UserError
from .store import Store

STATIC = {"app.css": "text/css; charset=utf-8", "app.js": "text/javascript; charset=utf-8",
          "icon.svg": "image/svg+xml"}
NO_WINDOW = 0x08000000 if os.name == "nt" else 0
_SYNC_LINE = re.compile(r"^\s{2}(.+?): (\+\d+ new|up to date)")


def _web(name: str) -> bytes:
    return resources.files("tgkit").joinpath("web", *name.split("/")).read_bytes()


def _python() -> str:
    """python.exe, not pythonw.exe: a child needs real stdout pipes."""
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe":
        alt = exe.with_name("python.exe")
        if alt.exists():
            return str(alt)
    return str(exe)


def _error(exc: UserError) -> dict:
    hint = exc.hint or ""
    needs = "setup" if "tgkit setup" in (exc.message + hint) else None
    return {"type": "error", "message": exc.message, "hint": hint, "needs": needs}


class App:
    def __init__(self, home: Path | None = None, claude: ClaudeRunner | None = None, token: str | None = None):
        self.home = Path(home) if home else config.home()
        self.claude = claude if claude is not None else ClaudeRunner()
        self.token = token or secrets.token_urlsafe(24)
        self.convs = Conversations(self.home)
        self._sync_busy = threading.Lock()
        self.last_seen = time.monotonic()

    def store(self) -> Store:
        return Store(self.home)

    # ------------------------------------------------------------- reads

    def state(self) -> dict:
        store = self.store()
        cfg = config.load(self.home) if (self.home / "config.json").exists() else {}
        return {
            "version": __version__,
            "claude": self.claude.available,
            "logged_in": config.logged_in(self.home),
            "has_codes": config.has_credentials(self.home, cfg),
            "chats": engine.chat_list(store),
            "conversations": self.convs.index(),
            "syncing": self._sync_busy.locked(),
        }

    # ------------------------------------------------------------- ask

    def ask(self, body: dict):
        text = (body.get("text") or "").strip()
        if not text:
            yield {"type": "error", "message": "Type a question first.", "hint": ""}
            return
        conv = self.convs.get(body["conversation"]) if body.get("conversation") else self.convs.new(text)
        yield {"type": "conversation", "id": conv["id"], "title": conv["title"]}
        self.convs.add_turn(conv, "user", text=text)
        store = self.store()
        want = body.get("mode") or "auto"
        mode = "claude" if want != "offline" and self.claude.available else "offline"
        notice = None
        if mode == "claude":
            yield {"type": "mode", "mode": "claude"}
            steps, cards, final, failed = {}, [], None, None
            titles = [t for _, t in engine.chat_names(store)]
            for ev in self.claude.stream(text, self.home, conv.get("claude_session"), titles):
                kind = ev["type"]
                if kind == "session":
                    conv["claude_session"] = ev["id"]
                elif kind == "error":
                    failed = ev
                    break
                elif kind == "step":
                    steps[ev["id"]] = {"command": ev["command"], "ok": None}
                    yield ev
                elif kind == "step_done":
                    if ev["id"] in steps:
                        steps[ev["id"]]["ok"] = ev["ok"]
                    yield ev
                elif kind == "tool_json":
                    card = engine.card_from_json(ev["command"], ev["data"])
                    if card:
                        cards.append(card)
                        yield {"type": "cards", "cards": [card]}
                elif kind == "token":
                    yield ev
                elif kind == "final":
                    final = ev["text"]
            if failed is None and final is not None:
                turn = self.convs.add_turn(conv, "assistant", mode="claude", text=final,
                                           steps=list(steps.values()), cards=cards)
                yield {"type": "done", "mode": "claude", "text": final, "turn": turn}
                return
            failed = failed or {"message": "Claude stopped without an answer."}
            notice = failed["message"] + " Answered without Claude."
            yield {"type": "notice", "text": notice, "reason": failed.get("reason")}
            mode = "offline"

        yield {"type": "mode", "mode": "offline"}
        try:
            it = intent.parse(text, engine.chat_names(store))
            res = engine.answer(self.home, store, it)
        except UserError as exc:
            err = _error(exc)
            self.convs.add_turn(conv, "assistant", mode="offline", text=exc.render(), error=True)
            yield err
            return
        turn = self.convs.add_turn(conv, "assistant", mode="offline", text=res["text"], cards=res["cards"],
                                   intent=it.to_dict(), action=res["action"], notice=notice)
        yield {"type": "cards", "cards": res["cards"]}
        yield {"type": "done", "mode": "offline", "text": res["text"], "action": res["action"], "turn": turn}

    # ------------------------------------------------------------- sync

    def sync(self):
        if not self._sync_busy.acquire(blocking=False):
            yield {"type": "error", "message": "A sync is already running.", "hint": ""}
            return
        try:
            total = len(self.store().chat_dirs())
            yield {"type": "start", "chats": total}
            env = dict(os.environ, TGKIT_HOME=str(self.home), PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
            try:
                proc = subprocess.Popen([_python(), "-m", "tgkit", "sync"], env=env,
                                        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, creationflags=NO_WINDOW)
            except OSError as exc:
                yield {"type": "error", "message": f"Could not start the sync ({exc}).", "hint": ""}
                return
            done, added, text = 0, 0, []
            try:
                for raw in proc.stdout:
                    line = raw.decode("utf-8", "replace").rstrip()
                    if not line:
                        continue
                    text.append(line)
                    m = _SYNC_LINE.match(line)
                    if m or line.startswith("Skipped "):
                        done += 1
                    if m and m.group(2).startswith("+"):
                        added += int(re.sub(r"\D", "", m.group(2)))
                    yield {"type": "progress", "line": line, "done": min(done, total), "chats": total}
            finally:
                if proc.poll() is None:
                    proc.kill()
                proc.wait()
            joined = "\n".join(text)
            yield {"type": "done", "code": proc.returncode, "added": added,
                   "needs": "setup" if "tgkit setup" in joined else None,
                   "last": text[-1] if text else ""}
        finally:
            self._sync_busy.release()

    def open_setup(self) -> dict:
        """Start `tgkit setup` in its own window: a Telegram login code is typed only there."""
        env = dict(os.environ, TGKIT_HOME=str(self.home))
        if os.name == "nt":
            subprocess.Popen(["cmd", "/k", _python(), "-m", "tgkit", "setup"], env=env,
                             creationflags=0x00000010)  # CREATE_NEW_CONSOLE
            return {"ok": True}
        if sys.platform == "darwin":
            subprocess.Popen(["osascript", "-e", 'tell application "Terminal" to do script "tgkit setup"'])
            return {"ok": True}
        return {"ok": False, "hint": "Open a terminal and type: tgkit setup"}


# ----------------------------------------------------------------- HTTP


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        server_version = "TGKit"

        def log_message(self, *args):  # quiet: no request log on a console
            pass

        # helpers --------------------------------------------------------

        def _host_ok(self) -> bool:
            host = (self.headers.get("Host") or "").split(":")[0]
            return host in ("127.0.0.1", "localhost")

        def _auth(self) -> bool:
            if not self._host_ok() or not secrets.compare_digest(
                    self.headers.get("X-TGKit-Token", ""), app.token):
                self._json({"type": "error", "message": "Not allowed."}, 403)
                return False
            app.last_seen = time.monotonic()
            return True

        def _send(self, body: bytes, ctype: str, code: int = 200):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data, code: int = 200):
            self._send(json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8", code)

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0 or n > 1_000_000:
                return {}
            try:
                data = json.loads(self.rfile.read(n).decode("utf-8"))
            except ValueError:
                return {}
            return data if isinstance(data, dict) else {}

        def _stream(self, events):
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                for ev in events:
                    self.wfile.write((json.dumps(ev, ensure_ascii=False) + "\n").encode("utf-8"))
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass  # the user pressed Stop or closed the window
            except UserError as exc:
                try:
                    self.wfile.write((json.dumps(_error(exc), ensure_ascii=False) + "\n").encode("utf-8"))
                except OSError:
                    pass
            finally:
                close = getattr(events, "close", None)
                if close:
                    close()

        # routes ---------------------------------------------------------

        def do_GET(self):
            url = urlparse(self.path)
            path = url.path
            if path in ("/", "/index.html"):
                if not self._host_ok():
                    return self._send(b"Not allowed", "text/plain", 403)
                html = _web("index.html").decode("utf-8").replace("__TGKIT_TOKEN__", app.token)
                return self._send(html.encode("utf-8"), "text/html; charset=utf-8")
            if path.startswith("/static/"):
                name = path[len("/static/"):]
                if name in STATIC:
                    return self._send(_web(f"static/{name}"), STATIC[name])
                return self._send(b"Not found", "text/plain", 404)
            if not path.startswith("/api/") or not self._auth():
                if not path.startswith("/api/"):
                    self._send(b"Not found", "text/plain", 404)
                return
            try:
                if path == "/api/state":
                    return self._json(app.state())
                if path == "/api/ping":
                    return self._json({"ok": True})
                m = re.fullmatch(r"/api/conversations/([\w-]+)", path)
                if m:
                    return self._json(app.convs.get(m.group(1)))
                if path == "/api/thread":
                    q = parse_qs(url.query)
                    folder = (q.get("folder") or [""])[0]
                    ident = int((q.get("id") or ["0"])[0])
                    return self._json({"items": engine.thread(app.store(), folder, ident)})
            except UserError as exc:
                return self._json(_error(exc), 400)
            except ValueError:
                return self._json({"type": "error", "message": "That request was not understood."}, 400)
            self._json({"type": "error", "message": "Not found."}, 404)

        def do_POST(self):
            path = urlparse(self.path).path
            if not path.startswith("/api/") or not self._auth():
                if not path.startswith("/api/"):
                    self._send(b"Not found", "text/plain", 404)
                return
            body = self._body()
            try:
                if path == "/api/ask":
                    return self._stream(app.ask(body))
                if path == "/api/sync":
                    return self._stream(app.sync())
                if path == "/api/setup":
                    return self._json(app.open_setup())
                m = re.fullmatch(r"/api/tasks/(\d+)", path)
                if m:
                    t = engine.set_task(app.home, m.group(1), str(body.get("status") or ""))
                    return self._json({"task": engine.tasks_card([t])["items"][0]})
                m = re.fullmatch(r"/api/conversations/([\w-]+)/archive", path)
                if m:
                    app.convs.archive(m.group(1))
                    return self._json({"ok": True})
            except UserError as exc:
                return self._json(_error(exc), 400)
            self._json({"type": "error", "message": "Not found."}, 404)

    return Handler


def serve(app: App, port: int = 0) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app))
    httpd.daemon_threads = True
    return httpd
