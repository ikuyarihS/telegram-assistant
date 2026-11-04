"""The Telegram Assistant app's local server: the web UI plus a small JSON API on 127.0.0.1.

Only this computer can reach it (it binds 127.0.0.1), and every /api call
must carry the random token written into the page, so another web page open
in a browser cannot call it. Answers stream as NDJSON (one JSON per line).

It reads through engine (the same code the CLI runs), writes only
tasks.json (task buttons), the app's own conversation files, usage.jsonl,
the app settings in config.json and the files the user saves (export), and
gets new messages by running `tgkit sync` itself (a tg-crawl import: `tgkit
import-crawl`), so the sync lock and every rule in docs/MAINTENANCE.md apply
unchanged. Log out goes through account.logout, under the same sync lock.
"""

import contextlib
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

from . import __version__, account, ai, applog, config, engine, export, intent, setupflow, updater, usage
from . import claude as claudemod
from . import codex as codexmod
from . import connectors
from . import gemini as geminimod
from .claude import ClaudeRunner
from .codex import CodexRunner
from .gemini import GeminiRunner
from .conversations import Conversations
from .errors import UserError, is_vi, localize
from .net import SWITCHED_LINE
from .store import OWNER_ENV, STOP_FILE, Store, _ident, _username, atomic_write, is_dead_link, lock_status

STATIC = {"app.css": "text/css; charset=utf-8", "app.js": "text/javascript; charset=utf-8",
          "icon.svg": "image/svg+xml", "favicon.png": "image/png", "icon-256.png": "image/png",
          "tgkit.ico": "image/x-icon"}
SYNC_STOP_WAIT = 15  # seconds a sync asked to stop gets to end cleanly before it is ended
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


_JOB = None  # Windows: the job the window's own `tgkit sync` runs in (made once, held until exit)
_JOB_LOCK = threading.Lock()


def _job_handle():
    """A Windows job object that ends every process in it when this server ends, even
    when the server is killed (JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE: the system closes
    the handle). Only processes this server started are put in it. None elsewhere,
    or when Windows refuses."""
    global _JOB
    if os.name != "nt":
        return None
    with _JOB_LOCK:
        if _JOB is not None:
            return _JOB or None
        _JOB = 0
        try:
            import ctypes  # noqa: PLC0415
            from ctypes import wintypes  # noqa: PLC0415

            class _Basic(ctypes.Structure):
                _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                            ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                            ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                            ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                            ("SchedulingClass", wintypes.DWORD)]

            class _Extended(ctypes.Structure):
                _fields_ = [("BasicLimitInformation", _Basic), ("IoInfo", ctypes.c_uint64 * 6),
                            ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                            ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.CreateJobObjectW.restype = wintypes.HANDLE
            k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
            k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
            k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
            k32.CloseHandle.argtypes = [wintypes.HANDLE]
            job = k32.CreateJobObjectW(None, None)
            if not job:
                return None
            info = _Extended()
            info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
                k32.CloseHandle(job)
                return None
            _JOB = (k32, job)
        except Exception as exc:  # noqa: BLE001
            applog.caught("window: no job object for the sync", exc)
            return None
        return _JOB


def _end_with_server(proc) -> bool:
    """Put a process this server just started into its job: it then ends when the
    server ends, however the server ends. A venv launcher's own child (the lock
    holder) is in the job too: a process started inside a job stays in it."""
    job = _job_handle()
    if not job:
        return False
    k32, handle = job
    try:
        return bool(k32.AssignProcessToJobObject(handle, int(proc._handle)))
    except Exception:  # noqa: BLE001
        return False


# The only links /api/open-link opens, each for a chat that is in Telegram Assistant (see
# store.telegram_link). (pattern, which chats it may name, has a message number)
_OPEN_FORMS = (
    (re.compile(r"https://t\.me/c/(\d{1,20})/(\d{1,12})"), "channel_id"),
    (re.compile(r"https://t\.me/([A-Za-z0-9_]{4,64})/(\d{1,12})"), "channel_user"),
    (re.compile(r"tg://privatepost\?channel=(\d{1,20})(?:&post=(\d{1,12}))?"), "channel_id"),
    (re.compile(r"tg://resolve\?domain=([A-Za-z0-9_]{4,64})(?:&post=(\d{1,12}))?"), "username"),
)
TELEGRAM_CODES_URL = "https://my.telegram.org/apps"
CHECK_TIMEOUT = 20  # seconds the account check at window start may take (offline: skipped)


def _known_chat(store: Store, key: str, value: str, post: str | None) -> dict | None:
    """The channel.json of the followed chat a link names, or None."""
    for d in store.chat_dirs():
        meta = store.meta(d)
        kind, ident, user = meta.get("type"), _ident(meta), (_username(meta) or "").lower()
        ok = ((key == "channel_id" and kind == "Channel" and ident == value)
              or (key == "channel_user" and kind == "Channel" and user == value.lower())
              or (key == "username" and user == value.lower() and (kind == "Channel" or not post)))
        if ok:
            return {**meta, "_title": store.title(d)}
    return None


def _has_tg_handler() -> bool:
    """Is the Telegram app registered for tg:// links (Windows)?"""
    try:
        import winreg  # noqa: PLC0415

        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"tg\shell\open\command"):
            return True
    except OSError:
        return False


def _error(exc: UserError, lang: str | None = None) -> dict:
    """A problem the user can fix. It is answered with HTTP 200 and user_error: the
    page shows it as a message, and the browser logs no failed request for it."""
    hint = exc.hint or ""
    needs = "setup" if "tgkit setup" in (exc.message + hint) else None
    return {"type": "error", "user_error": True, "ok": False, "message": localize(exc.message, lang),
            "hint": localize(hint, lang) or "", "needs": needs}


# The folder picker: tkinter's dialog in a child Python (Tk wants its own main thread).
_PICK_CODE = ("import sys, tkinter; from tkinter import filedialog; r = tkinter.Tk(); r.withdraw(); "
              "r.attributes('-topmost', True); "
              "p = filedialog.askdirectory(parent=r, title=sys.argv[1], initialdir=sys.argv[2], mustexist=True); "
              "r.destroy(); sys.stdout.write(p or '')")


def _local_import(ev: dict, lang: str | None) -> dict:
    """An import event in the window's language: its error, and each sentence of each row note."""
    if not is_vi(lang):
        return ev
    if ev.get("type") == "error":
        return {**ev, "message": localize(ev.get("message"), lang), "hint": localize(ev.get("hint"), lang)}
    if ev.get("type") == "chat" and ev.get("notes"):
        return {**ev, "note": " ".join(localize(n, lang) for n in ev["notes"])}
    if ev.get("type") == "done":
        rows = [{**r, "note": " ".join(localize(n, lang) for n in r.get("notes") or [])} for r in ev.get("rows") or []]
        return {**ev, "rows": rows, "account_note": localize(ev.get("account_note"), lang)}
    return ev


def _local_event(ev, lang: str | None):
    """A streamed error or notice in the window's language."""
    if not is_vi(lang) or not isinstance(ev, dict):
        return ev
    if ev.get("type") == "error":
        return {**ev, "message": localize(ev.get("message"), lang), "hint": localize(ev.get("hint"), lang)}
    if ev.get("type") == "notice":
        return {**ev, "text": localize(ev.get("text"), lang)}
    return ev


class App:
    def __init__(self, home: Path | None = None, claude: ClaudeRunner | None = None, token: str | None = None,
                 gemini: GeminiRunner | None = None, gpt: CodexRunner | None = None):
        self.home = Path(home) if home else config.home()
        self.claude = claude if claude is not None else ClaudeRunner()
        self.gemini = gemini if gemini is not None else GeminiRunner()
        self.gpt = gpt if gpt is not None else CodexRunner()
        self._gstatus = None
        self._ostatus = None  # GPT (Codex) status, cached like Gemini's
        self._install_busy = threading.Lock()  # one Gemini install at a time
        self.token = token or secrets.token_urlsafe(24)
        self.convs = Conversations(self.home)
        self._sync_busy = threading.Lock()
        self._check_busy = threading.Lock()  # the account check when the window opens
        self._proc = None  # the window's own `tgkit sync`, while it runs
        self._proc_owner = None  # the token that `tgkit sync` writes into the lock file
        # The window's own sync as a page loaded later sees it (/api/running): its last
        # progress line while it runs, then its "done" event.
        self._sync_view: dict = {"line": "", "done": 0, "chats": 0}
        self._sync_last: dict | None = None
        self._settings_lock = threading.Lock()
        self._pick_busy = threading.Lock()  # one folder picker at a time
        self._status = None
        self.tg = setupflow.TelegramSetup(self.home)  # setup inside the window
        self.last_seen = time.monotonic()
        self._update_busy = threading.Lock()  # one update download at a time
        self.quit_hook = None  # app.py: closes the window (Update finishes after it closes)

    def store(self) -> Store:
        return Store(self.home)

    # ------------------------------------------------------------- reads

    def state(self) -> dict:
        store = self.store()
        cfg = config.load(self.home) if (self.home / "config.json").exists() else {}
        return {
            "version": __version__,
            "claude": self.claude.available,
            "ai": self.provider(),
            "ai_choice": ai.choice(self.home),
            "logged_in": config.logged_in(self.home),
            "has_codes": config.has_credentials(self.home, cfg),
            "chats": engine.chat_list(store),
            "conversations": self.convs.index(),
            "syncing": self._sync_busy.locked(),
            "adding": self.tg.adding,
            "account": (account.owner(self.home) or {}).get("name"),
        }

    # ------------------------------------------------------------- the account and the sync lock

    def account_check(self) -> dict:
        """When the window opens: ask Telegram who the login file belongs to and, for
        another account than the chats shown, switch to that account's chats (R15).
        Skipped (nothing changes) when offline, not logged in or busy."""
        none = {"checked": False, "switched": False}
        if not account.has_session(self.home) or not config.has_credentials(self.home, self._cfg()):
            return none
        if self.tg.step or self.tg.adding or not self._check_busy.acquire(blocking=False):
            return none
        try:
            if self._sync_busy.locked():
                return none
            import asyncio  # noqa: PLC0415

            from . import net  # noqa: PLC0415

            ctx = net.Ctx(self.home, self.store(), self._cfg())
            res = net.run(asyncio.wait_for(net.adopt_session(ctx), CHECK_TIMEOUT))
        except UserError as exc:
            return {**none, "reason": exc.message}
        except Exception as exc:  # noqa: BLE001
            applog.caught("window start: account check", exc, self.home)
            return none
        finally:
            self._check_busy.release()
        return {"checked": True, "me": res.get("me"), **setupflow._switch_fields(res),
                "notes": account.switch_notes(res)}

    def running(self) -> dict:
        """What of this window runs now, for a page loaded while it runs (first open,
        reload, a reopened tab): it shows that task's progress and its Stop."""
        sync_on = self._sync_busy.locked()
        return {"sync": {"running": sync_on, **(self._sync_view if sync_on else {"line": "", "done": 0, "chats": 0}),
                         "last": None if sync_on else self._sync_last},
                "copy": self.tg.adding}

    def lock_info(self) -> dict:
        """Who holds the sync lock and whether it looks hung; can_stop when the holder
        is this window's own task (its sync, its copy of ticked chats, its login)."""
        st = lock_status(self.home)
        return {**st, "can_stop": bool(st["held"] and (self._own_child(st) or st["mine"]))}

    def _own_child(self, st: dict) -> bool:
        """Is the lock held by the `tgkit sync` this window started? Known by the token
        it was given (a venv launcher puts a process between, so the pid differs),
        or by its pid when it is the direct child."""
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return False
        return bool((self._proc_owner and st.get("owner") == self._proc_owner) or st.get("pid") == proc.pid)

    def _end_own_sync(self, st: dict) -> None:
        """End the window's own sync: only processes it can prove are its own. The
        lock holder is proved by the token it wrote (a venv launcher puts it one
        process below the child), the launcher by the handle the window holds. No
        process-tree walk: Windows keeps stale parent ids after a pid is reused, so a
        walk could end an unrelated program."""
        proc = self._proc
        if proc is None:
            return
        import signal  # noqa: PLC0415

        holder = st.get("pid")
        now = lock_status(self.home)
        # The holder is ended only while the lock is still HELD under this window's token:
        # a holder that crashed leaves its pid and token in the file, and that pid may
        # belong to another program by now (gate 0.2.5 NOTE 1).
        if (holder and holder != proc.pid and self._proc_owner and st.get("owner") == self._proc_owner
                and now.get("held") and now.get("owner") == self._proc_owner
                and str(now.get("pid")) == str(holder)):
            try:
                # SIGTERM is TerminateProcess on Windows (signal 1 there is a Ctrl+Break)
                os.kill(int(holder), signal.SIGTERM if os.name == "nt" else signal.SIGKILL)
            except (OSError, ValueError):
                pass
        if proc.poll() is None:
            proc.kill()  # the launcher (or the holder itself when it is the direct child)

    def close(self) -> None:
        """The window is closing or the server is ending: end the window's own `tgkit
        sync` (its launcher and the lock holder it proves by its token), the same rules
        as Stop and retry. Never another program. A sync ended partway commits nothing
        partial: the sync guard is unchanged and every file is written whole or not at
        all (R1); the next sync fetches the rest."""
        proc = self._proc
        if self.tg.adding:
            with contextlib.suppress(Exception):
                self.tg.stop(wait=5)
        if proc is None or proc.poll() is not None:
            return
        try:
            self._ask_sync_stop(proc, 5)  # first asked: it ends cleanly, keeps what it saved
            if proc.poll() is None:
                st = lock_status(self.home)
                if st.get("held") and self._own_child(st):
                    self._end_own_sync(st)
                elif proc.poll() is None:
                    proc.kill()  # the launcher only: a handle this window holds
            proc.wait(timeout=10)
        except Exception as exc:  # noqa: BLE001
            applog.caught("window close: ending its own sync", exc, self.home)

    def _ask_sync_stop(self, proc, wait: float) -> bool:
        """Ask the window's own `tgkit sync` to stop (STOP_FILE with its token) and wait
        for it to end. True when it ended."""
        if not self._proc_owner:
            return False
        atomic_write(self.home / STOP_FILE, self._proc_owner)
        try:
            proc.wait(timeout=wait)
        except subprocess.TimeoutExpired:
            return False
        return True

    def stop_running(self) -> dict:
        """Stop, the button shown the whole time a copy or a sync runs: ends it cleanly.
        A copy keeps every chat saved and marks the rest Stopped (Retry continues them);
        a sync keeps every chat it saved, the guard drops a chat read halfway, and the
        next Get new messages gets the rest. Never another program."""
        stopped = False
        if self.tg.adding:
            self.tg.stop()
            stopped = True
        proc = self._proc
        if proc is not None and proc.poll() is None:
            if not self._ask_sync_stop(proc, SYNC_STOP_WAIT) and proc.poll() is None:
                st = lock_status(self.home)
                if st.get("held") and self._own_child(st):
                    self._end_own_sync(st)
                elif proc.poll() is None:
                    proc.kill()
            stopped = True
        return {"ok": True, "stopped": stopped, "lock": self.lock_info()}

    def stop_lock(self) -> dict:
        """Stop and retry: end this window's own hung task so the system frees its
        lock. Never another program, never a file deleted (an interrupted write
        leaves the old file or the new one, R1)."""
        st = self.lock_info()
        if not st["held"]:
            return {"ok": True, "lock": st}
        if not st["can_stop"]:
            raise UserError("Another Telegram Assistant window or terminal is using this data folder.",
                            "Close it, then press Get new messages again. Telegram Assistant never stops another program.")
        if self._own_child(st):
            self._end_own_sync(st)  # the window's own `tgkit sync`: the system frees its lock
        else:
            self.tg.stop()
        deadline = time.monotonic() + 15
        while lock_status(self.home)["held"] and time.monotonic() < deadline:
            time.sleep(0.1)
        st = self.lock_info()
        if st["held"]:
            raise UserError("Telegram Assistant could not stop the hung task.",
                            "Close Telegram Assistant and open it again: that always frees it. Nothing was deleted.")
        return {"ok": True, "lock": st}

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
        if body.get("pattern"):  # "Pattern (advanced)": a regular expression, matched here (no Claude)
            yield {"type": "mode", "mode": "offline"}
            try:
                res = engine.pattern_answer(store, text, "vi" if is_vi(body.get("lang")) else "en")
            except UserError as exc:
                self.convs.add_turn(conv, "assistant", mode="offline", text=exc.render(), error=True)
                yield _error(exc)
                return
            turn = self.convs.add_turn(conv, "assistant", mode="offline", text=res["text"], cards=res["cards"],
                                       pattern=True)
            yield {"type": "cards", "cards": res["cards"]}
            yield {"type": "done", "mode": "offline", "text": res["text"], "action": None, "turn": turn}
            return
        want = body.get("mode") or "auto"
        provider = self.provider() if want != "offline" else None
        mode = provider or "offline"
        name = ai.NAMES.get(provider or "", "")
        notice = None
        if provider:
            lim = usage.limit_state(self.home, self._cfg())
            if lim["reached"] and lim["hard"]:
                notice = (f"Today's {name} limit that you set is reached ({lim['used']:,} of {lim['limit']:,} "
                          f"tokens). Answered without {name}.")
                yield {"type": "notice", "text": notice, "reason": "limit"}
                mode = "offline"
            elif lim["reached"]:
                yield {"type": "notice", "reason": "limit_soft",
                       "text": f"You are over the daily {name} limit you set ({lim['used']:,} of "
                               f"{lim['limit']:,} tokens). {name} still answers; change it in Settings."}
        if mode != "offline":
            runner = {"claude": self.claude, "gemini": self.gemini, "gpt": self.gpt}[provider]
            key = f"{provider}_session"  # each AI resumes only its own session
            yield {"type": "mode", "mode": provider}
            steps, cards, final, failed = {}, [], None, None
            model, used = None, None
            titles = [t for _, t in engine.chat_names(store)]
            for ev in runner.stream(text, self.home, conv.get(key), titles):
                kind = ev["type"]
                if kind == "session":
                    conv[key] = ev["id"]
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
                usage.record(self.home, used, conv["id"], model, provider)
            if failed is None and final is not None:
                extra = {"model": model} if model else {}
                if used:
                    extra["usage"] = {k: v for k, v in used.items() if k != "models"}
                turn = self.convs.add_turn(conv, "assistant", mode=provider, text=final,
                                           steps=list(steps.values()), cards=cards, **extra)
                yield {"type": "done", "mode": provider, "text": final, "turn": turn}
                return
            if failed and failed.get("reason") == "login":  # check again: Settings and the next pick see it
                if provider == "gemini":
                    self._gstatus = None
                elif provider == "gpt":
                    self._ostatus = None
                else:
                    self._status = None
            failed = failed or {"message": f"{name} stopped without an answer."}
            notice = failed["message"] + f" Answered without {name}."
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
        if self.tg.step:
            yield {"type": "error", "message": "A Telegram login is under way in the setup window.",
                   "hint": "Finish it or close the setup, then press Get new messages again."}
            return
        if self.tg.adding:
            yield {"type": "error", "message": "Telegram Assistant is still copying the chats you ticked.",
                   "hint": "Wait until it is done, then press Get new messages again."}
            return
        if not self._sync_busy.acquire(blocking=False):
            yield {"type": "error", "message": "A sync is already running.", "hint": ""}
            return
        try:
            self._sync_view, self._sync_last = {"line": "", "done": 0, "chats": 0}, None
            self._after_check()
            total = len(self.store().chat_dirs())
            self._sync_view = {"line": "", "done": 0, "chats": total}
            yield {"type": "start", "chats": total}
            owner = secrets.token_hex(8)
            env = dict(os.environ, TGKIT_HOME=str(self.home), PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1",
                       **{OWNER_ENV: owner})
            try:
                proc = subprocess.Popen([_python(), "-m", "tgkit", "sync"], env=env,
                                        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, creationflags=NO_WINDOW)
            except OSError as exc:
                yield {"type": "error", "message": f"Could not start the sync ({exc}).", "hint": ""}
                return
            _end_with_server(proc)  # ends with this window, even when the window is killed
            self._proc, self._proc_owner = proc, owner
            yield {"type": "running", "stop": True}  # the Stop button shows from here on
            done, added, text, switched = 0, 0, [], False
            try:
                for raw in proc.stdout:
                    line = raw.decode("utf-8", "replace").rstrip()
                    if not line:
                        continue
                    text.append(line)
                    if line.startswith(SWITCHED_LINE):
                        switched = True
                        total = len(self.store().chat_dirs())  # now the logged-in account's chats
                        yield {"type": "switched", "line": line}
                    m = _SYNC_LINE.match(line)
                    if m or line.startswith("Skipped "):
                        done += 1
                    if m and m.group(2).startswith("+"):
                        added += int(re.sub(r"\D", "", m.group(2)))
                    self._sync_view = {"line": line, "done": min(done, total), "chats": total}
                    yield {"type": "progress", "line": line, "done": min(done, total), "chats": total}
            finally:
                if proc.poll() is None:
                    proc.kill()
                proc.wait()
                self._proc, self._proc_owner = None, None
                with contextlib.suppress(OSError):
                    (self.home / STOP_FILE).unlink()  # a control file, never data
            joined = "\n".join(text)
            ev = {"type": "done", "code": proc.returncode, "added": added, "switched": switched,
                  "stopped": any(t.startswith("Stopped: ") for t in text),
                  "needs": "setup" if "tgkit setup" in joined else None,
                  "last": text[-1] if text else ""}
            if proc.returncode and "already running" in joined:
                ev["lock"] = self.lock_info()  # who holds it, and whether it looks hung
            self._sync_last = ev
            yield ev
        finally:
            self._sync_busy.release()

    # ------------------------------------------------------------- import a tg-crawl copy

    def import_preview(self, body: dict) -> dict:
        """What Import would bring in (reads only, nothing is copied)."""
        from . import crawlimport  # noqa: PLC0415

        return crawlimport.preview(self.home, body.get("folder"))

    def import_crawl(self, body: dict, lang: str | None = None):
        """Import runs `tgkit import-crawl --events` as this window's own child, like its sync:
        the window never writes a chat itself (R12), the same Stop ends it cleanly, and
        closing the window ends it (a chat half copied is left as it was)."""
        folder = str(body.get("folder") or "").strip()
        if self.tg.step:
            yield {"type": "error", "message": "A Telegram login is under way in the setup window.",
                   "hint": "Finish it or close the setup, then try again."}
            return
        if self.tg.adding:
            yield {"type": "error", "message": "Telegram Assistant is still copying the chats you ticked.",
                   "hint": "Wait until it is done, then try again."}
            return
        if not self._sync_busy.acquire(blocking=False):
            yield {"type": "error", "message": "New messages are being fetched right now.",
                   "hint": "Wait until it is done, then try again."}
            return
        try:
            self._after_check()
            self._sync_view = {"line": "", "done": 0, "chats": 0}
            owner = secrets.token_hex(8)
            env = dict(os.environ, TGKIT_HOME=str(self.home), PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1",
                       **{OWNER_ENV: owner})
            try:
                proc = subprocess.Popen([_python(), "-m", "tgkit", "import-crawl", folder, "--events"], env=env,
                                        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, creationflags=NO_WINDOW)
            except OSError as exc:
                yield {"type": "error", "message": f"Could not start the import ({exc}).", "hint": ""}
                return
            _end_with_server(proc)
            self._proc, self._proc_owner = proc, owner
            yield {"type": "running", "stop": True}
            ended = False
            try:
                for raw in proc.stdout:
                    try:
                        ev = json.loads(raw.decode("utf-8", "replace"))
                    except ValueError:
                        continue  # a warning line, not an event
                    if not isinstance(ev, dict):
                        continue
                    ended = ended or ev.get("type") in ("done", "error")
                    if ev.get("type") == "chat" and ev.get("state") == "copying":
                        self._sync_view = {**self._sync_view, "line": str(ev.get("chat") or "")}
                    yield _local_import(ev, lang)
            finally:
                if proc.poll() is None:
                    proc.kill()
                proc.wait()
                self._proc, self._proc_owner = None, None
                with contextlib.suppress(OSError):
                    (self.home / STOP_FILE).unlink()  # a control file, never data
            if not ended:
                yield {"type": "error", "message": "The import ended without a report.",
                       "hint": "Import again: chats already imported are not copied twice, and the tg-crawl "
                               "folder was not changed."}
        finally:
            self._sync_busy.release()

    def pick_folder(self, body: dict) -> dict:
        """The computer's own folder picker, opened by this local server in a child Python
        (a web page cannot read a folder path). No picker: type the path instead."""
        if not self._pick_busy.acquire(blocking=False):
            return {"ok": False, "path": "", "busy": True}
        try:
            start = str(body.get("start") or Path.home())
            title = "Pick the tg-crawl folder" if not is_vi(body.get("lang")) else "Chọn thư mục tg-crawl"
            try:
                done = subprocess.run([_python(), "-I", "-c", _PICK_CODE, title, start], capture_output=True,
                                      timeout=900, creationflags=NO_WINDOW)
            except (OSError, subprocess.TimeoutExpired):
                return {"ok": False, "path": "", "hint": "Type or paste the folder path instead."}
            if done.returncode != 0:
                return {"ok": False, "path": "", "hint": "Type or paste the folder path instead."}
            path = done.stdout.decode("utf-8", "replace").strip()
            return {"ok": bool(path), "path": str(Path(path)) if path else ""}
        finally:
            self._pick_busy.release()

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

    def gemini_status(self, refresh: bool = False) -> dict:
        """Cached: checking runs `node --version` and `gemini --version`."""
        if refresh or self._gstatus is None:
            if refresh and self.gemini.cmd is None:  # installed since the app opened? (cmd=[] means "off")
                self.gemini.cmd = geminimod.find_gemini()
            self._gstatus = geminimod.status(self.gemini.cmd or [], self.home)
        return self._gstatus

    def gpt_status(self, refresh: bool = False) -> dict:
        """Cached: checking runs `codex --version` and `codex login status`."""
        if refresh or self._ostatus is None:
            if refresh and self.gpt.cmd is None:  # installed since the app opened? (cmd=[] means "off")
                self.gpt.cmd = codexmod.find_codex()
            self._ostatus = codexmod.status(self.gpt.cmd or [], self.home)
        return self._ostatus

    def provider(self) -> str | None:
        """The AI that answers now (ai.pick): Settings' choice, else Claude first, then
        Gemini, then GPT, each when it is signed in. Checks only what the choice needs, but
        checks it (cached), so the window picks like `tgkit ai-status` from the first question."""
        chosen = ai.choice(self.home)
        if chosen == "off":
            return None
        gemini_state = claude_state = gpt_state = None
        if chosen == "auto" and self.gemini.available:
            gemini_state = self.gemini_status()["state"]
        if chosen == "auto" and self.gpt.available and gemini_state != "connected":
            gpt_state = self.gpt_status()["state"]
        if chosen == "auto" and self.claude.available and "connected" in (gemini_state, gpt_state):
            claude_state = self.claude_status()["state"]  # logged in? else another one answers
        return ai.pick(chosen, self.claude.available, claude_state, self.gemini.available, gemini_state,
                       self.gpt.available, gpt_state)

    def ai_status(self, refresh: bool = False) -> dict:
        st_claude = self.claude_status(refresh)
        st_gemini = self.gemini_status(refresh)
        st_gpt = self.gpt_status(refresh)
        return {"choice": ai.choice(self.home), "active": self.provider() or "off",
                "claude": st_claude, "gemini": st_gemini, "gpt": st_gpt}

    def settings(self) -> dict:
        cfg = self._cfg()
        st = self.claude_status()
        return {
            "ai_provider": ai.choice(self.home),
            "model": str(cfg.get("claude_model") or ""), "effort": str(cfg.get("claude_effort") or ""),
            "models": st.get("models") or list(claudemod.BASE_MODELS) if st.get("installed") else [],
            "efforts": st.get("efforts") or [],
            "usage_daily_limit": int(cfg.get("usage_daily_limit") or 0),
            "usage_hard_stop": bool(cfg.get("usage_hard_stop")),
            "export_dir": str(cfg.get("export_dir") or ""), "export_default": str(export.default_dir()),
            "export_now": str(export.export_dir(self.home)),
            "max_media_mb": config.max_media_bytes(self.home, cfg) // (1024 * 1024),
            "media": config.media_mode(cfg), "media_modes": list(config.MEDIA_MODES),
            "home": str(self.home),
            "update_check": updater.enabled(self.home), "version": __version__,
            "account": {"logged_in": config.logged_in(self.home), "name": account.who(self.home),
                        "chats": account.data_summary(self.home)["chats"]},
        }

    def save_settings(self, body: dict) -> dict:
        """Change only the keys sent, each checked; everything else in config.json stays.
        The change runs under the config lock that every config.json writer takes."""
        st = self.claude_status()

        def change(cfg: dict) -> None:
            if "ai_provider" in body:
                chosen = str(body.get("ai_provider") or "")
                if chosen not in ai.CHOICES:
                    raise UserError("Pick Claude, Gemini, GPT or Off.",
                                    "Automatic picks Claude first, then Gemini, then GPT.")
                cfg["ai_provider"] = chosen
            if "model" in body:
                model = str(body.get("model") or "").strip()
                allowed = set(st.get("models") or claudemod.BASE_MODELS)
                if model and model not in allowed:
                    raise UserError(f"'{model}' is not a model Telegram Assistant can choose.", "Pick one from the list.")
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
                    raise UserError("Type the full path of the folder.", r"For example: D:\Reports\Telegram Assistant")
                cfg["export_dir"] = folder
            if "max_media_mb" in body:
                try:
                    mb = int(body.get("max_media_mb"))
                except (TypeError, ValueError):
                    raise UserError("The file size limit must be a whole number of MB.", "For example: 50") from None
                if not 1 <= mb <= 4000:
                    raise UserError("The file size limit must be between 1 and 4000 MB.", "For example: 50")
                cfg["max_media_mb"] = mb
            if "media" in body:
                mode = str(body.get("media") or "")
                if mode not in config.MEDIA_MODES:
                    raise UserError("Pick one of the Files choices.", "Text and small files is the usual one.")
                cfg["media"] = mode
            if updater.SETTING in body:
                cfg[updater.SETTING] = bool(body.get(updater.SETTING))
        config.update(self.home, change)
        return self.settings()

    # ------------------------------------------------------------- Check for updates (0.2.10)

    def update_check(self, quiet: bool = False) -> dict:
        return updater.check(self.home, quiet=quiet)

    def update_start(self) -> dict:
        """Download and check the newest release, then close the window: the update
        finishes in its own window after this one is gone, and opens the new version."""
        kit = updater.kit_dir()
        if not kit:
            raise updater.not_installed()
        if not self._update_busy.acquire(blocking=False):
            raise UserError("The update is being downloaded already.", "Wait until it is done.")
        try:
            info = updater.newest_or_error()
            new = updater.download(info, kit)
            res = updater.start(kit, new)
        finally:
            self._update_busy.release()
        hook = self.quit_hook
        if hook:
            threading.Timer(1.5, hook).start()  # after the answer reached the page
        return {**res, "version": info["version"], "closing": bool(hook)}

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
        if self.tg.adding:
            raise UserError("Telegram Assistant is still copying the chats you ticked.", "Wait until it is done, then log out.")
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

    # ------------------------------------------------------------- Gemini setup (Settings)

    def _console(self, argv: list[str], cwd: Path | None = None, mac_line: str = "") -> dict:
        """Open argv in its own console window (Windows) or Terminal (macOS). argv runs
        directly, not through cmd.exe, so a path with spaces needs no quoting rules."""
        if os.environ.get("TGKIT_NO_REVEAL"):  # tests: say what would open, open nothing
            return {"ok": True, "argv": argv}
        try:
            if os.name == "nt":
                subprocess.Popen(argv, cwd=str(cwd) if cwd else None, creationflags=geminimod.NEW_CONSOLE)
                return {"ok": True}
            if sys.platform == "darwin" and mac_line:
                subprocess.Popen(["osascript", "-e", f'tell application "Terminal" to do script "{mac_line}"'])
                return {"ok": True}
        except OSError:
            pass
        return {"ok": False, "hint": f"Open a terminal and type: {mac_line or ' '.join(argv)}"}

    def gemini_login(self) -> dict:
        """Sign in to Google: the Gemini CLI in its own window asks how to sign in and
        opens the Google page in the browser."""
        cmd = self.gemini.cmd if self.gemini.available else geminimod.find_gemini()
        if not cmd:
            return {"ok": False, "hint": "Install Gemini first."}
        work = self.home / "app" / "gemini"
        work.mkdir(parents=True, exist_ok=True)
        geminimod.clear_signin_failed(self.home)
        self._gstatus = None
        return self._console(geminimod.login_argv(cmd), work, "gemini")

    def gemini_node(self) -> dict:
        """Install Node.js: winget in its own window when Windows has it, else the download page."""
        if geminimod.winget():
            res = self._console(list(geminimod.WINGET_NODE))
            if res.get("ok"):
                return {**res, "how": "winget"}
        return {**self._browse(geminimod.NODE_URL), "how": "browser"}

    def _npm_install(self, package: str, name: str) -> tuple[int, str] | dict:
        """`npm install -g <package>`: (exit code, output tail), or a refusal dict."""
        npm = geminimod.find_npm()
        if not npm:
            return {"ok": False, "message": "Node.js is not installed yet.", "hint": "Press Install Node.js first."}
        if not self._install_busy.acquire(blocking=False):
            return {"ok": False, "message": "Something is being installed already.", "hint": "Wait until it is done."}
        try:
            env = dict(os.environ)
            node = geminimod.find_node()
            if node:  # npm needs node on PATH, also when Node.js came after the app opened
                env["PATH"] = os.pathsep.join([str(Path(node).parent), env.get("PATH", "")])
            try:
                done = subprocess.run([*npm, "install", "-g", package], stdin=subprocess.DEVNULL,
                                      capture_output=True, timeout=900, creationflags=NO_WINDOW, env=env)
                return done.returncode, ((done.stdout or b"") + (done.stderr or b"")).decode(
                    "utf-8", "replace").strip()[-600:]
            except (OSError, subprocess.TimeoutExpired) as exc:
                return -1, str(exc)
        finally:
            self._install_busy.release()

    def gemini_install(self) -> dict:
        """`npm install -g @google/gemini-cli`, then check that the gemini command is really
        there: an install that ends without it is reported as failed, never as done."""
        res = self._npm_install(geminimod.PACKAGE, "Gemini")
        if isinstance(res, dict):
            return res
        code, tail = res
        self.gemini.cmd = geminimod.find_gemini()
        self._gstatus = None
        st = self.gemini_status()
        if code == 0 and st["installed"]:
            return {"ok": True, "status": st}
        return {"ok": False, "status": st, "detail": tail,
                "message": "Gemini could not be installed.",
                "hint": "Check the internet connection and press Install Gemini again."}

    # ------------------------------------------------------------- GPT (Codex) setup (Settings)

    def gpt_install(self) -> dict:
        """`npm install -g @openai/codex`, then check that Codex really starts."""
        res = self._npm_install(codexmod.PACKAGE, "GPT")
        if isinstance(res, dict):
            return res
        code, tail = res
        self.gpt.cmd = codexmod.find_codex()
        self._ostatus = None
        st = self.gpt_status()
        if code == 0 and st["installed"]:
            return {"ok": True, "status": st}
        return {"ok": False, "status": st, "detail": tail, "message": "GPT (Codex) could not be installed.",
                "hint": "Check the internet connection and press Install GPT again."}

    def gpt_login(self) -> dict:
        """Sign in to ChatGPT: `codex login` in its own window opens the ChatGPT page."""
        cmd = self.gpt.cmd if self.gpt.available else codexmod.find_codex()
        if not cmd:
            return {"ok": False, "hint": "Install GPT first."}
        self._ostatus = None
        return self._console(codexmod.login_argv(cmd), None, "codex login")

    # ------------------------------------------------------------- connectors (Settings)

    def connectors_view(self) -> dict:
        return {"browser": connectors.browser_view(self.home), "mcp": connectors.mcp_view(self.home)}

    def browser_set(self, body: dict) -> dict:
        connectors.set_browser(self.home, bool(body.get("enabled")), accept=bool(body.get("accept")),
                               token=body.get("token") if "token" in body else None)
        return self.connectors_view()

    def browser_install(self) -> dict:
        """The browser helper for Gemini and GPT: `npm install -g @playwright/mcp`."""
        res = self._npm_install(connectors.BRIDGE_PACKAGE, "the browser helper")
        if isinstance(res, dict):
            return res
        code, tail = res
        view = self.connectors_view()
        if code == 0 and view["browser"]["bridge"]["installed"]:
            return {"ok": True, **view}
        return {"ok": False, **view, "detail": tail, "message": "The browser helper could not be installed.",
                "hint": "Check the internet connection and press Install again."}

    def open_store(self, body: dict) -> dict:
        """Open an extension's store page: only these two known addresses."""
        url = {"claude": connectors.CLAUDE_EXTENSION_URL, "bridge": connectors.BRIDGE_EXTENSION_URL}.get(
            str(body.get("which") or ""))
        if not url:
            raise UserError("Telegram Assistant does not know that page.", "")
        return self._browse(url)

    def mcp_action(self, action: str, body: dict) -> dict:
        cid = str(body.get("id") or "")
        if action == "add":
            return connectors.mcp_add(self.home, str(body.get("name") or ""), str(body.get("target") or ""))
        if action == "allow":
            return connectors.mcp_allow(self.home, cid, body.get("tools") or [])
        if action == "refresh":
            return connectors.mcp_refresh(self.home, cid)
        if action == "remove":
            return connectors.mcp_remove(self.home, cid)
        raise UserError("Unknown connector action.", "")

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

    def open_link(self, body: dict, lang: str | None = None) -> dict:
        """Open a message or a chat in Telegram: the Telegram app when it is installed,
        else the browser. Only the link forms of store.telegram_link, and only for a
        chat that is in Telegram Assistant; the link opened is rebuilt from that chat's own data."""
        url = str(body.get("url") or "")
        if is_dead_link(url):  # never opened: Telegram Desktop has no handler for these
            raise UserError("Telegram Desktop cannot open this kind of link.",
                            "Open Telegram and find the chat by its name.")
        for pattern, key in _OPEN_FORMS:
            m = pattern.fullmatch(url)
            if m:
                break
        else:
            m = None
        post = m.group(2) if m and m.re.groups >= 2 else None
        meta = _known_chat(self.store(), key, m.group(1), post) if m else None
        if meta is None:
            raise UserError("That is not a link to a chat in Telegram Assistant.", "Open it from a result in this window.")
        user, ident = _username(meta), _ident(meta)
        web = None  # the same place on t.me, for a computer without Telegram Desktop
        if meta.get("type") == "Channel" and post:
            target = f"tg://resolve?domain={user}&post={post}" if user else f"tg://privatepost?channel={ident}&post={post}"
            web = f"https://t.me/{user}/{post}" if user else f"https://t.me/c/{ident}/{post}"
            opens = "message"
        elif user:  # a username opens the chat in every Telegram app, Desktop included
            target, web, opens = f"tg://resolve?domain={user}", f"https://t.me/{user}", "chat"
        else:  # a channel without a username, the chat itself
            target, opens = f"tg://privatepost?channel={ident}", "chat"
        note = None
        if os.name == "nt" and not _has_tg_handler() and not os.environ.get("TGKIT_NO_REVEAL"):
            if not web:
                raise UserError("Telegram Desktop is not on this computer.",
                                "Install it from desktop.telegram.org, then press the link again.")
            target, note = web, None
        res = {"ok": True, "url": target, "opens": opens, "note": note}
        if os.environ.get("TGKIT_NO_REVEAL"):
            return res
        if target.startswith("tg://") and os.name == "nt":
            try:
                os.startfile(target)  # noqa: S606  (the Telegram app's own link handler)
                return res
            except OSError:
                if not web:
                    raise UserError("Telegram Desktop is not on this computer.",
                                    "Install it from desktop.telegram.org, then press the link again.") from None
                target, res["note"] = web, None
        import webbrowser  # noqa: PLC0415

        return {**res, "ok": bool(webbrowser.open(target)), "url": target}

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

    def open_logs(self) -> dict:
        folder = applog.log_dir(self.home)
        folder.mkdir(parents=True, exist_ok=True)
        export.reveal(folder)
        return {"ok": True, "folder": str(folder)}

    def diagnostics(self) -> dict:
        """Text for Copy diagnostics: no messages, codes, phone numbers or passwords."""
        cfg = self._cfg()

        def lock_text(name: str) -> str:
            try:
                return (self.home / name).read_text(encoding="utf-8", errors="replace")[:200].strip() or "free"
            except OSError:
                return "none"
        facts = {
            "Window": "yes", "Telegram codes saved": config.has_credentials(self.home, cfg),
            "Logged in": config.logged_in(self.home), "Chats": len(self.store().chat_dirs()),
            "Sync running in this window": self._sync_busy.locked(),
            "Login under way": self.tg.step or "no",
            "sync.lock": lock_text("sync.lock"), "session.lock": lock_text("session.lock"),
            "Claude": "available" if self.claude.available else "not available",
            "Gemini": "available" if self.gemini.available else "not available",
            "AI answers": f"{ai.choice(self.home)} (now: {self.provider() or 'off'})",
        }
        return {"text": applog.diagnostics(self.home, facts)}

    # ------------------------------------------------------------- setup

    # Setup talks to Telegram only when no sync does: plain words, no wait.
    def _after_check(self) -> None:
        """The account check at window start holds the sync lock a moment: wait for it."""
        if self._check_busy.acquire(timeout=CHECK_TIMEOUT + 5):
            self._check_busy.release()

    def setup_dialogs(self) -> dict:
        if self._sync_busy.locked():
            raise UserError("Telegram Assistant is still getting new messages.", "Wait until it is done, then open Add chats again.")
        self._after_check()
        return self.tg.dialogs()

    def setup_add(self, body: dict) -> dict:
        if self._sync_busy.locked():
            raise UserError("Telegram Assistant is still getting new messages.", "Wait until it is done, then press Add again.")
        self._after_check()
        media = body.get("files")
        if media is not None and media not in config.MEDIA_MODES:
            raise UserError("Pick one of the Files choices.", "Text and small files is the usual one.")
        return self.tg.add(body.get("ids"), body.get("limit", 1000), keep=bool(body.get("keep")), media=media)

    def setup_folder(self, body: dict) -> dict:
        """The first setup step: keep the data folder Telegram Assistant uses now, or pick another.
        The choice is saved like `tgkit setup` saves it (~/.tgkit-home)."""
        if os.environ.get("TGKIT_HOME"):
            raise UserError("The data folder is set by TGKIT_HOME, so it cannot be changed here.",
                            "Keep it, or ask the person who set up this computer.")
        if self.tg.step:
            raise UserError("A login is under way.", "Finish it or close the setup, then change the folder.")
        if self.tg.adding:
            raise UserError("Telegram Assistant is still copying the chats you ticked.",
                            "Wait until it is done, then change the folder.")
        if self._sync_busy.locked():
            raise UserError("New messages are being fetched right now.", "Wait until it is done, then change the folder.")
        if str(body.get("choice") or "keep") == "keep":
            target = self.home
        else:
            raw = str(body.get("path") or "").strip().strip('"')
            if not raw or not Path(raw).expanduser().is_absolute():
                raise UserError("Type the full path of the folder.", r"For example: D:\Telegram Assistant")
            target = Path(raw).expanduser()
        if target.exists() and not target.is_dir():
            raise UserError("That is a file, not a folder.", "Pick a folder.")
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise UserError(f"That folder could not be made ({exc.strerror or exc}).", "Pick another folder.") from None
        config.remember_home(target)
        if target != self.home:
            self.tg.cancel()
            self.home = target
            self.convs = Conversations(target)
            self.tg = setupflow.TelegramSetup(target)
            self._status = None
        return self.tg.state()

    def setup_step(self, step: str, body: dict) -> dict:
        if step == "codes":
            return self.tg.codes(body.get("api_id"), body.get("api_hash"))
        if self._sync_busy.locked():
            raise UserError("New messages are being fetched right now.", "Wait until it is done, then log in.")
        self._after_check()
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

        def _lang(self) -> str:
            return (self.headers.get("X-TGKit-Lang") or "en").strip().lower()[:8]

        def _auth(self) -> bool:
            if not self._host_ok() or not secrets.compare_digest(
                    self.headers.get("X-TGKit-Token", ""), app.token):
                self._json({"type": "error", "message": localize("Not allowed.", self._lang())}, 403)
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
            """Read the whole request body. It is read before any answer, even a 403:
            an answer sent while the client is still sending can reset the connection."""
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = 0
            if n > 1_000_000:
                self.close_connection = True  # too big to read: answer, then close
                return {}
            if n <= 0:
                return {}
            try:
                data = json.loads(self.rfile.read(n).decode("utf-8"))
            except ValueError:
                return {}
            return data if isinstance(data, dict) else {}

        def _stream(self, events, drain: bool = False):
            """drain=True (the sync): a page that goes away (reload, closed tab) does not
            end the task; it runs on, a page loaded later follows it with /api/running
            and ends it with Stop. Closing the window still ends it (App.close)."""
            lang = self._lang()
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                for ev in events:
                    ev = _local_event(ev, lang)
                    self.wfile.write((json.dumps(ev, ensure_ascii=False) + "\n").encode("utf-8"))
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                # the user pressed Stop on an answer, or the page went away
                if drain:
                    with contextlib.suppress(Exception):
                        for _ev in events:
                            pass
            except UserError as exc:
                try:
                    self.wfile.write((json.dumps(_error(exc, lang), ensure_ascii=False) + "\n").encode("utf-8"))
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
                    flag = (q.get("refresh") or [""])[0].strip().lower()
                    return self._json(app.claude_status(flag in ("1", "true", "yes")))
                if path == "/api/ai-status":
                    q = parse_qs(url.query)
                    flag = (q.get("refresh") or [""])[0].strip().lower()
                    return self._json(app.ai_status(flag in ("1", "true", "yes")))
                if path == "/api/connectors":
                    return self._json(app.connectors_view())
                if path == "/api/update/check":
                    q = parse_qs(url.query)
                    return self._json(app.update_check((q.get("quiet") or [""])[0] in ("1", "true", "yes")))
                if path == "/api/usage":
                    q = parse_qs(url.query)
                    return self._json(app.usage((q.get("conversation") or [None])[0]))
                if path == "/api/setup/state":
                    return self._json(app.tg.state())
                if path == "/api/setup/dialogs":
                    return self._json(app.setup_dialogs())
                if path == "/api/setup/add-progress":
                    st, lang = app.tg.add_state(), self._lang()
                    for row in st["rows"]:  # each chat's error in the window's language
                        row["message"], row["hint"] = localize(row.get("message"), lang), localize(row.get("hint"), lang)
                    if st.get("error"):
                        st["error"] = {k: localize(v, lang) for k, v in st["error"].items()}
                    return self._json({**st, "lock": app.lock_info()})
                if path == "/api/lock":
                    return self._json(app.lock_info())
                if path == "/api/running":
                    return self._json(app.running())
                if path == "/api/diagnostics":
                    return self._json(app.diagnostics())
                m = re.fullmatch(r"/api/conversations/([\w-]+)", path)
                if m:
                    return self._json(app.convs.get(m.group(1)))
                if path == "/api/thread":
                    q = parse_qs(url.query)
                    folder = (q.get("folder") or [""])[0]
                    ident = int((q.get("id") or ["0"])[0])
                    return self._json({"items": engine.thread(app.store(), folder, ident)})
            except UserError as exc:
                return self._json(_error(exc, self._lang()))
            except ValueError:
                return self._json({"type": "error", "message": localize("That request was not understood.",
                                                                         self._lang())}, 400)
            except Exception as exc:  # noqa: BLE001
                return self._unexpected(path, exc)
            self._json({"type": "error", "message": localize("Not found.", self._lang())}, 404)

        def _unexpected(self, path: str, exc: BaseException):
            """An error nobody planned for: logged with its traceback, shown in plain words."""
            applog.caught(f"window {self.command} {path}", exc, app.home)
            return self._json(_error(UserError(
                "Something went wrong in Telegram Assistant.",
                "Try again in a moment. If it keeps happening, press Copy diagnostics in Settings "
                "and send it to whoever gave you Telegram Assistant."), self._lang()))

        def do_POST(self):
            path = urlparse(self.path).path
            body = self._body()  # first: see _body
            if not path.startswith("/api/") or not self._auth():
                if not path.startswith("/api/"):
                    self._send(b"Not found", "text/plain", 404)
                return
            try:
                if path == "/api/ask":
                    return self._stream(app.ask(body))
                if path == "/api/sync":
                    return self._stream(app.sync(), drain=True)
                if path == "/api/import/preview":
                    return self._json(app.import_preview(body))
                if path == "/api/import":
                    return self._stream(app.import_crawl(body, self._lang()), drain=True)
                if path == "/api/pick-folder":
                    return self._json(app.pick_folder({**body, "lang": self._lang()}))
                if path == "/api/setup":
                    return self._json(app.open_setup())
                if path == "/api/setup/folder":
                    return self._json(app.setup_folder(body))
                m = re.fullmatch(r"/api/setup/(codes|phone|code|password|cancel)", path)
                if m:
                    return self._json(app.setup_step(m.group(1), body))
                if path == "/api/setup/add":
                    return self._json(app.setup_add(body))
                if path == "/api/sync/stop":  # Stop and retry: whatever of this window holds the sync lock
                    return self._json(app.stop_lock())
                if path == "/api/stop":  # Stop: the copy or the sync running now, ended cleanly
                    return self._json(app.stop_running())
                if path == "/api/account/check":
                    return self._json(app.account_check())
                if path == "/api/setup/open-site":
                    return self._json(app.open_telegram_site())
                if path == "/api/open-data":
                    return self._json(app.open_data())
                if path == "/api/open-logs":
                    return self._json(app.open_logs())
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
                if path == "/api/gemini/login":
                    return self._json(app.gemini_login())
                if path == "/api/gemini/node":
                    return self._json(app.gemini_node())
                installs = {"/api/gemini/install": app.gemini_install, "/api/gpt/install": app.gpt_install,
                            "/api/connectors/browser/install": app.browser_install}
                if path in installs:
                    res = installs[path]()
                    lang = self._lang()
                    return self._json({**res, "message": localize(res.get("message"), lang),
                                       "hint": localize(res.get("hint"), lang)})
                if path == "/api/gpt/login":
                    return self._json(app.gpt_login())
                if path == "/api/gpt/node":
                    return self._json(app.gemini_node())
                if path == "/api/connectors/browser":
                    return self._json(app.browser_set(body))
                if path == "/api/connectors/open":
                    return self._json(app.open_store(body))
                m = re.fullmatch(r"/api/connectors/mcp/(add|allow|refresh|remove)", path)
                if m:
                    return self._json(app.mcp_action(m.group(1), body))
                if path == "/api/update/start":
                    return self._json(app.update_start())
                if path == "/api/reveal":
                    return self._json(app.reveal(body))
                if path == "/api/open-link":
                    return self._json(app.open_link(body, self._lang()))
                m = re.fullmatch(r"/api/tasks/(\d+)", path)
                if m:
                    t = engine.set_task(app.home, m.group(1), str(body.get("status") or ""))
                    return self._json({"task": engine.tasks_card([t])["items"][0]})
                m = re.fullmatch(r"/api/conversations/([\w-]+)/archive", path)
                if m:
                    app.convs.archive(m.group(1))
                    return self._json({"ok": True})
            except UserError as exc:
                return self._json(_error(exc, self._lang()))
            except Exception as exc:  # noqa: BLE001
                return self._unexpected(path, exc)
            self._json({"type": "error", "message": localize("Not found.", self._lang())}, 404)

    return Handler


def serve(app: App, port: int = 0) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app))
    httpd.daemon_threads = True
    return httpd
