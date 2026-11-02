"""The TG Kit app's local server: the web UI plus a small JSON API on 127.0.0.1.

Only this computer can reach it (it binds 127.0.0.1), and every /api call
must carry the random token written into the page, so another web page open
in a browser cannot call it. Answers stream as NDJSON (one JSON per line).

It reads through engine (the same code the CLI runs), writes only
tasks.json (task buttons), the app's own conversation files, usage.jsonl,
the app settings in config.json and the files the user saves (export), and
gets new messages by running `tgkit sync` itself, so the sync lock and every
rule in docs/MAINTENANCE.md apply unchanged. Log out goes through
account.logout, under the same sync lock.
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

from . import __version__, account, config, engine, export, intent, setupflow, usage
from . import claude as claudemod
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


_TG_LINK = re.compile(r"https://t\.me/(?:c/(\d{1,20})|([A-Za-z0-9_]{4,64}))/(\d{1,12})")
TELEGRAM_CODES_URL = "https://my.telegram.org/apps"


def _has_tg_handler() -> bool:
    """Is the Telegram app registered for tg:// links (Windows)?"""
    try:
        import winreg  # noqa: PLC0415

        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"tg\shell\open\command"):
            return True
    except OSError:
        return False


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
        self._settings_lock = threading.Lock()
        self._status = None
        self.tg = setupflow.TelegramSetup(self.home)  # setup inside the window
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
            lim = usage.limit_state(self.home, self._cfg())
            if lim["reached"] and lim["hard"]:
                notice = (f"Today's Claude limit that you set is reached ({lim['used']:,} of {lim['limit']:,} "
                          "tokens). Answered without Claude.")
                yield {"type": "notice", "text": notice, "reason": "limit"}
                mode = "offline"
            elif lim["reached"]:
                yield {"type": "notice", "reason": "limit_soft",
                       "text": f"You are over the daily Claude limit you set ({lim['used']:,} of "
                               f"{lim['limit']:,} tokens). Claude still answers; change it in Settings."}
        if mode == "claude":
            yield {"type": "mode", "mode": "claude"}
            steps, cards, final, failed = {}, [], None, None
            model, used = None, None
            titles = [t for _, t in engine.chat_names(store)]
            for ev in self.claude.stream(text, self.home, conv.get("claude_session"), titles):
                kind = ev["type"]
                if kind == "session":
                    conv["claude_session"] = ev["id"]
                elif kind == "model":
                    model = ev["model"]
                elif kind == "usage":
                    used = ev["usage"]
                    if not model and used.get("models"):
                        model = used["models"][0]
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
            if used:
                # Tokens are spent even when the answer then failed: count them.
                usage.record(self.home, used, conv["id"], model)
            if failed is None and final is not None:
                extra = {"model": model} if model else {}
                if used:
                    extra["usage"] = {k: v for k, v in used.items() if k != "models"}
                turn = self.convs.add_turn(conv, "assistant", mode="claude", text=final,
                                           steps=list(steps.values()), cards=cards, **extra)
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

    # ------------------------------------------------------------- settings

    def _cfg(self) -> dict:
        return config.load(self.home) if config.config_path(self.home).exists() else {}

    def claude_status(self, refresh: bool = False) -> dict:
        """Cached: checking runs `claude --version`, `--help` and `auth status`."""
        if refresh or self._status is None:
            if refresh:
                claudemod._HELP_CACHE.clear()
                if self.claude.cmd is None:  # installed since the app opened? (cmd=[] means "off")
                    self.claude.cmd = claudemod.find_claude()
            self._status = claudemod.status(self.claude.cmd or [], self.home)
        return {**self._status, **claudemod.settings(self.home)}

    def settings(self) -> dict:
        cfg = self._cfg()
        st = self.claude_status()
        return {
            "model": str(cfg.get("claude_model") or ""), "effort": str(cfg.get("claude_effort") or ""),
            "models": st.get("models") or list(claudemod.BASE_MODELS) if st.get("installed") else [],
            "efforts": st.get("efforts") or [],
            "usage_daily_limit": int(cfg.get("usage_daily_limit") or 0),
            "usage_hard_stop": bool(cfg.get("usage_hard_stop")),
            "export_dir": str(cfg.get("export_dir") or ""), "export_default": str(export.default_dir()),
            "export_now": str(export.export_dir(self.home)),
            "max_media_mb": config.max_media_bytes(self.home, cfg) // (1024 * 1024),
            "home": str(self.home),
            "account": {"logged_in": config.logged_in(self.home), "name": account.who(self.home),
                        "chats": account.data_summary(self.home)["chats"]},
        }

    def save_settings(self, body: dict) -> dict:
        """Change only the keys sent, each checked; everything else in config.json stays."""
        with self._settings_lock:
            cfg = self._cfg()
            st = self.claude_status()
            if "model" in body:
                model = str(body.get("model") or "").strip()
                allowed = set(st.get("models") or claudemod.BASE_MODELS)
                if model and model not in allowed:
                    raise UserError(f"'{model}' is not a model TG Kit can choose.", "Pick one from the list.")
                cfg["claude_model"] = model
            if "effort" in body:
                effort = str(body.get("effort") or "").strip()
                if effort and effort not in (st.get("efforts") or []):
                    raise UserError(f"This Claude Code has no effort level '{effort}'.", "Pick one from the list.")
                cfg["claude_effort"] = effort
            if "usage_daily_limit" in body:
                try:
                    limit = int(body.get("usage_daily_limit") or 0)
                except (TypeError, ValueError):
                    raise UserError("The daily limit must be a whole number of tokens.", "Use 0 for no limit.") from None
                if limit < 0:
                    raise UserError("The daily limit cannot be below 0.", "Use 0 for no limit.")
                cfg["usage_daily_limit"] = limit
            if "usage_hard_stop" in body:
                cfg["usage_hard_stop"] = bool(body.get("usage_hard_stop"))
            if "export_dir" in body:
                folder = str(body.get("export_dir") or "").strip().strip('"')
                if folder and not Path(folder).expanduser().is_absolute():
                    raise UserError("Type the full path of the folder.", r"For example: D:\Reports\TG Kit")
                cfg["export_dir"] = folder
            if "max_media_mb" in body:
                try:
                    mb = int(body.get("max_media_mb"))
                except (TypeError, ValueError):
                    raise UserError("The file size limit must be a whole number of MB.", "For example: 50") from None
                if not 1 <= mb <= 4000:
                    raise UserError("The file size limit must be between 1 and 4000 MB.", "For example: 50")
                cfg["max_media_mb"] = mb
            config.save(self.home, cfg)
        return self.settings()

    def usage(self, conversation: str | None = None) -> dict:
        tot = usage.totals(self.home, conversation)
        tot["limit"] = usage.limit_state(self.home, self._cfg())
        return tot

    def export(self, body: dict) -> dict:
        kind = str(body.get("kind") or "")
        fmt = str(body.get("format") or "docx")
        doc = export.build(self.home, kind, query=body.get("query"), chat=body.get("chat"),
                           since=body.get("since"), which=body.get("which") or "open",
                           conversation=body.get("conversation"), card=body.get("card"),
                           lang=str(body.get("lang") or "en"))
        path = export.save(self.home, doc, fmt)
        export.reveal(path)
        return {"ok": True, "path": str(path), "name": path.name, "folder": str(path.parent)}

    def save_file(self, body: dict) -> dict:
        path = export.save_attachment(self.home, str(body.get("folder") or ""), body.get("id"))
        export.reveal(path)
        return {"ok": True, "path": str(path), "name": path.name, "folder": str(path.parent)}

    def logout(self, body: dict) -> dict:
        if self._sync_busy.locked():
            raise UserError("New messages are being fetched right now.", "Wait until it is done, then log out.")
        res = account.logout(self.home, str(body.get("data") or "keep"))
        return {"ok": True, **res}

    def _terminal(self, argv: list[str], mac_line: str) -> dict:
        """Open a terminal window running argv (Windows, macOS); elsewhere say what to type."""
        try:
            if os.name == "nt":
                subprocess.Popen(["cmd", "/k", *argv], creationflags=0x00000010)  # CREATE_NEW_CONSOLE
                return {"ok": True}
            if sys.platform == "darwin":
                subprocess.Popen(["osascript", "-e", f'tell application "Terminal" to do script "{mac_line}"'])
                return {"ok": True}
        except OSError:
            pass
        return {"ok": False, "hint": f"Open a terminal and type: {mac_line}"}

    def claude_login(self) -> dict:
        cmd = self.claude.cmd if self.claude.available else claudemod.find_claude()
        if not cmd:
            return {"ok": False, "hint": "Install Claude Code first."}
        auth = claudemod.capabilities(cmd).get("auth")
        self._status = None
        return self._terminal([*cmd, "auth", "login"] if auth else list(cmd),
                              "claude auth login" if auth else "claude")

    def reveal(self, body: dict) -> dict:
        """Show a saved file: only one inside the export folder."""
        folder = export.export_dir(self.home).resolve()
        try:
            path = Path(str(body.get("path") or "")).resolve()
        except (OSError, ValueError):
            path = None
        if not path or not path.is_relative_to(folder) or not path.exists():
            raise UserError("That file is not in your export folder any more.", "Open the folder from Settings.")
        return {"ok": export.reveal(path)}

    def open_link(self, body: dict) -> dict:
        """Open a message in Telegram: the Telegram app when it is installed, else the browser."""
        url = str(body.get("url") or "")
        m = _TG_LINK.fullmatch(url)
        if not m:
            raise UserError("That is not a link to a Telegram message.", "")
        target = url
        if os.name == "nt" and _has_tg_handler():
            chan, user, msg = m.group(1), m.group(2), m.group(3)
            target = (f"tg://privatepost?channel={chan}&post={msg}" if chan
                      else f"tg://resolve?domain={user}&post={msg}")
        if os.environ.get("TGKIT_NO_REVEAL"):
            return {"ok": True, "url": target}
        try:
            if target.startswith("tg://"):
                os.startfile(target)  # noqa: S606  (the Telegram app's own link handler)
                return {"ok": True, "url": target}
        except OSError:
            target = url
        import webbrowser  # noqa: PLC0415

        return {"ok": bool(webbrowser.open(target)), "url": target}

    def open_install(self) -> dict:
        return self._browse(claudemod.INSTALL_URL)

    def _browse(self, url: str) -> dict:
        if os.environ.get("TGKIT_NO_REVEAL"):
            return {"ok": True, "url": url}
        import webbrowser  # noqa: PLC0415

        return {"ok": bool(webbrowser.open(url)), "url": url}

    def open_telegram_site(self) -> dict:
        """my.telegram.org, where the two Telegram codes are made."""
        return self._browse(TELEGRAM_CODES_URL)

    def open_data(self) -> dict:
        self.home.mkdir(parents=True, exist_ok=True)
        export.reveal(self.home)
        return {"ok": True, "folder": str(self.home)}

    # ------------------------------------------------------------- setup

    def setup_add(self, body: dict) -> dict:
        if self._sync_busy.locked():
            raise UserError("New messages are being fetched right now.", "Wait until it is done, then add chats.")
        return self.tg.add(body.get("ids"), body.get("limit", 1000))

    def setup_step(self, step: str, body: dict) -> dict:
        if step == "codes":
            return self.tg.codes(body.get("api_id"), body.get("api_hash"))
        if self._sync_busy.locked():
            raise UserError("New messages are being fetched right now.", "Wait until it is done, then log in.")
        if step == "phone":
            return self.tg.phone(body.get("phone"))
        if step == "code":
            return self.tg.code(body.get("code"))
        if step == "password":
            return self.tg.password(str(body.get("password") or ""))
        return self.tg.cancel()


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
                if path == "/api/settings":
                    return self._json(app.settings())
                if path == "/api/claude-status":
                    q = parse_qs(url.query)
                    return self._json(app.claude_status(bool((q.get("refresh") or [""])[0])))
                if path == "/api/usage":
                    q = parse_qs(url.query)
                    return self._json(app.usage((q.get("conversation") or [None])[0]))
                if path == "/api/setup/state":
                    return self._json(app.tg.state())
                if path == "/api/setup/dialogs":
                    return self._json(app.tg.dialogs())
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
                m = re.fullmatch(r"/api/setup/(codes|phone|code|password|cancel)", path)
                if m:
                    return self._json(app.setup_step(m.group(1), body))
                if path == "/api/setup/add":
                    return self._json(app.setup_add(body))
                if path == "/api/setup/open-site":
                    return self._json(app.open_telegram_site())
                if path == "/api/open-data":
                    return self._json(app.open_data())
                if path == "/api/settings":
                    return self._json(app.save_settings(body))
                if path == "/api/export":
                    return self._json(app.export(body))
                if path == "/api/save-file":
                    return self._json(app.save_file(body))
                if path == "/api/open-folder":
                    return self._json({"ok": True, "folder": str(export.open_folder(app.home))})
                if path == "/api/logout":
                    return self._json(app.logout(body))
                if path == "/api/claude/login":
                    return self._json(app.claude_login())
                if path == "/api/claude/install":
                    return self._json(app.open_install())
                if path == "/api/reveal":
                    return self._json(app.reveal(body))
                if path == "/api/open-link":
                    return self._json(app.open_link(body))
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
