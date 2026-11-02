"""Setup inside the TG Kit window: Telegram codes, login, and picking chats.

The same steps as `tgkit setup`, one HTTP call per step, for people who never
open a terminal:

  codes(api_id, api_hash)  -> saved in config.json (checked first)
  phone(number)            -> Telegram sends a login code   -> "code"
  code(code)               -> logged in, or "password" (two-step verification)
  password(pw)             -> logged in
  dialogs()                -> the user's Telegram chats, with "added" flags
  add(ids, limit)          -> starts net.add_chats (the guarded add, under the sync
                              lock) in the background; add_state() has one row per
                              ticked chat: waiting, copying N, done or a plain error
  stop()                   -> cancels the copy (or a login) and frees its locks

The Telethon client of a login lives on one asyncio loop in a background
thread, so the code typed in the next request reaches the same client. The
sync lock is held from "send code" to the end of the login, so no sync uses
the login file meanwhile. Nothing here prints a code or a password, and
nothing is kept after the login but Telethon's own session file.
"""

import asyncio
import contextlib
import io
import os
import socket
import sqlite3
import threading
import time
from pathlib import Path

from . import account, applog, config
from .errors import UserError
from .store import SessionLock, Store, SyncLock

LOGIN_TIMEOUT = 15 * 60  # an unfinished login is dropped after this many seconds
LIST_WAIT = 10  # seconds listing chats waits for a short holder of the sync lock


BUSY = ("TG Kit is still busy with Telegram (getting new messages or copying chats).",
        "Wait a moment and try again.")
COPYING = ("TG Kit is still copying the chats you ticked.",
           "Wait until it is done, or press Stop and retry.")
UNKNOWN = ("Something went wrong while talking to Telegram.",
           "Try again in a moment. If it keeps happening, press Copy diagnostics in Settings "
           "and send it to whoever gave you TG Kit.")


# Plain words for what went wrong, never a bare error class name. Every error that
# is not a UserError is logged with its traceback first.
def _friendly(exc: BaseException, home=None) -> UserError:
    if isinstance(exc, UserError):
        return exc
    applog.caught("setup in the window", exc, home)
    from .cli import _FRIENDLY  # noqa: PLC0415

    name = type(exc).__name__
    if isinstance(exc, sqlite3.OperationalError) or name == "OperationalError":
        return UserError(*BUSY)  # the login file (SQLite) was in use by another client
    if name in _FRIENDLY:
        msg, hint = _FRIENDLY[name]
        hint = {"PhoneNumberInvalidError": "Type it with the country code, like +84 912 345 678.",
                "PhoneCodeInvalidError": "Check the code in your Telegram app and type it again.",
                "PhoneCodeExpiredError": "Press Send code again to get a new one.",
                "PasswordHashInvalidError": "Type your two-step password again.",
                "ApiIdInvalidError": "Copy api_id and api_hash again from my.telegram.org."}.get(name, hint)
        return UserError(msg, hint)
    if isinstance(exc, (ConnectionError, TimeoutError, asyncio.TimeoutError, socket.gaierror)):
        return UserError("Could not reach Telegram.", "Check your internet connection and try again.")
    if isinstance(exc, OSError):  # a file of the data folder, not the network
        return UserError("TG Kit could not read or write a file in its data folder.",
                         "Close any program that has a file of TG Kit open, then try again.")
    return UserError(*UNKNOWN)


class TelegramSetup:
    def __init__(self, home: Path):
        self.home = Path(home)
        self._mutex = threading.Lock()
        self._loop = None
        self._client = None
        self._phone = None
        self._hash = None
        self._lock = None
        self._session = None
        self._since = 0.0
        self.step = None  # None, "code" or "password" while a login is under way
        self._job = None  # the copy of ticked chats running in the background (add)
        self._job_lock = threading.Lock()

    # ------------------------------------------------------------- helpers

    def _cfg(self) -> dict:
        return config.load(self.home) if config.config_path(self.home).exists() else {}

    def _call(self, coro, timeout: float = 120):
        if self._loop is None:
            self._loop = asyncio.new_event_loop()
            threading.Thread(target=self._loop.run_forever, daemon=True, name="tgkit-login").start()
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return fut.result(timeout)
        except Exception as exc:  # noqa: BLE001
            raise _friendly(exc, self.home) from None

    def _end(self) -> None:
        """Close the login client and free both locks. Runs on every way out of a
        login, failures included: a lock left held would block every later sync."""
        client, self._client = self._client, None
        self._phone = self._hash = None
        self.step = None
        if client is not None:
            with contextlib.suppress(Exception):
                res = client.disconnect()
                if asyncio.iscoroutine(res):
                    self._call(res, 20)
        for name in ("_session", "_lock"):
            lock = getattr(self, name)
            setattr(self, name, None)
            if lock is not None:
                lock.release()

    def _fail(self, exc: BaseException):
        """A login step failed in a way typing again cannot fix: end the login."""
        self._end()
        raise _friendly(exc, self.home) from None

    def _expire(self) -> None:
        if self.step and time.monotonic() - self._since > LOGIN_TIMEOUT:
            self._end()

    def _done(self) -> dict:
        """Logged in: read who it is from Telegram, save it, and when it is another
        account than the chats shown, switch to that account's chats. The login
        ends (locks freed) whatever happens here."""
        try:
            me = self._call(self._client.get_me())
            from .net import display_name  # noqa: PLC0415

            who = {"id": me.id, "name": display_name(me), "username": getattr(me, "username", None)}
            res = account.adopt(self.home, who, locked=True)  # the login holds the sync lock
        finally:
            self._end()
        return {"step": "done", "me": who, "switched": bool(res.get("switched")),
                "parked": res.get("parked"), "comes_back": bool(res.get("comes_back")),
                "restored": bool(res.get("restored"))}

    # ------------------------------------------------------------- steps

    def state(self) -> dict:
        with self._mutex:
            self._expire()
            cfg = self._cfg()
            api_id = str(cfg.get("api_id") or "")
            has_codes, logged_in = config.has_credentials(self.home, cfg), config.logged_in(self.home)
            chats = len(Store(self.home).chat_dirs())
            return {"has_codes": has_codes,
                    "api_id": api_id, "logged_in": logged_in,
                    "me": cfg.get("me") if logged_in else None,
                    "step": self.step, "home": str(self.home),
                    "chats": chats,
                    # the data folder step: offered on a first setup, unless TGKIT_HOME fixes the folder
                    "home_fixed": bool(os.environ.get("TGKIT_HOME")),
                    "fresh": not has_codes and not logged_in and chats == 0}

    def codes(self, api_id: str, api_hash: str) -> dict:
        api_id, api_hash = str(api_id or "").strip(), str(api_hash or "").strip().replace(" ", "")
        if not config.valid_api_id(api_id):
            raise UserError("That api_id does not look right.", "It is a number, like 1234567.")
        if not config.valid_api_hash(api_hash):
            raise UserError("That api_hash does not look right.",
                            "It has 32 letters and digits. Copy it from the page and paste it here.")
        with self._mutex:
            self._end()
            config.put(self.home, api_id=api_id, api_hash=api_hash)
        return self.state()

    def phone(self, number: str) -> dict:
        number = "".join(ch for ch in str(number or "") if ch.isdigit() or ch == "+")
        if len(number.lstrip("+")) < 7:
            raise UserError("That phone number looks too short.", "Type it with the country code, like +84 912 345 678.")
        with self._mutex:
            self._end()
            cfg = self._cfg()
            from . import net  # noqa: PLC0415

            async def make():  # made on the login loop, where it will run
                return net.make_client(self.home, cfg)  # refuses in plain words without codes
            if self.adding:
                raise UserError(*COPYING)
            self._lock = SyncLock(self.home, "login").acquire()  # no sync uses the login file meanwhile
            try:
                self._session = SessionLock(self.home).acquire()  # nor any other client
                self._client = self._call(make())
                client = self._client
                self._call(client.connect())
                if self._call(client.is_user_authorized()):
                    return self._done()
                sent = self._call(client.send_code_request(number))
            except UserError:
                self._end()
                raise
            self._phone, self._hash = number, getattr(sent, "phone_code_hash", None)
            self.step, self._since = "code", time.monotonic()
            return {"step": "code"}

    def code(self, code: str) -> dict:
        code = "".join(ch for ch in str(code or "") if ch.isdigit())
        with self._mutex:
            self._expire()
            if self.step != "code" or self._client is None:
                raise UserError("The login code is no longer waited for.", "Press Send code again.")
            if not code:
                raise UserError("Type the code Telegram sent you.", "Look in your Telegram app.")
            client, phone, phash = self._client, self._phone, self._hash

            async def sign():
                try:
                    await client.sign_in(phone=phone, code=code, phone_code_hash=phash)
                except Exception as exc:  # noqa: BLE001
                    if type(exc).__name__ == "SessionPasswordNeededError":
                        return "password"
                    if type(exc).__name__ == "PhoneCodeExpiredError":
                        return "expired"
                    if type(exc).__name__ in ("PhoneCodeInvalidError", "PhoneCodeEmptyError"):
                        raise  # typing the code again fixes it: the login stays
                    return exc
                return "done"
            got = self._call(sign())
            if isinstance(got, BaseException):
                self._fail(got)
            if got == "password":
                self.step, self._since = "password", time.monotonic()
                return {"step": "password"}
            if got == "expired":
                self._end()
                raise _friendly(type("PhoneCodeExpiredError", (Exception,), {})())
            return self._done()

    def password(self, password: str) -> dict:
        with self._mutex:
            self._expire()
            if self.step != "password" or self._client is None:
                raise UserError("The password is no longer waited for.", "Start again with Send code.")
            if not password:
                raise UserError("Type your two-step password.", "It is the extra password you set in Telegram.")
            client = self._client

            async def sign():
                try:
                    await client.sign_in(password=password)
                except Exception as exc:  # noqa: BLE001
                    if type(exc).__name__ == "PasswordHashInvalidError":
                        raise  # typing it again fixes it: the login stays
                    return exc
                return None
            failed = self._call(sign())
            if failed is not None:
                self._fail(failed)
            return self._done()

    def cancel(self) -> dict:
        with self._mutex:
            self._end()
        return self.state()

    # ------------------------------------------------------------- chats

    def dialogs(self) -> dict:
        from . import net  # noqa: PLC0415

        if self.adding:
            raise UserError(*COPYING)
        ctx = net.Ctx(self.home, Store(self.home), self._cfg())

        async def fetch():
            # The sync lock first, then the client (R14): adopt may move chats. A short
            # holder (the account check when the window opens) is waited for.
            lock = SyncLock(self.home, "listing chats")
            lock.wait = LIST_WAIT
            with lock:
                async with net.connected(ctx) as client:
                    # Who the login file belongs to now: another account than the chats
                    # shown (a login made elsewhere) switches to that account's chats.
                    res = account.adopt(self.home, await net.session_me(client), locked=True)
                    found = await net.list_dialogs(client)
                    existing = {d.name for d in ctx.store.chat_dirs()}
                    keys, cut = net.followed_keys(ctx.store), net.partial_keys(ctx.store)
                    rows = []
                    for d in found:
                        # a first copy cut short stays tickable, marked, so Retry survives a restart
                        partial = net.chat_key(net.entity_meta(d["entity"])) in cut
                        added = (net.folder_for(d["entity"], d["name"]) in existing
                                 or net.chat_key(net.entity_meta(d["entity"])) in keys)
                        rows.append({"id": str(d["id"]), "name": d["name"], "kind": d["kind"],
                                     "added": added and not partial, "partial": partial})
                    return rows, res
        try:
            rows, res = net.run(fetch())
        except UserError as exc:
            if "already running" in exc.message:
                raise UserError(*BUSY) from None
            raise
        except Exception as exc:  # noqa: BLE001
            raise _friendly(exc, self.home) from None
        return {"dialogs": rows, **_switch_fields(res)}

    # ------------------------------------------------------------- copying ticked chats

    @property
    def adding(self) -> bool:
        job = self._job
        return bool(job and job["running"])

    def add(self, ids, limit, keep: bool = False) -> dict:
        """Start copying the ticked chats in the background; add_state() follows it.
        keep=True (a Retry of one chat) keeps the rows of the chats copied before."""
        wanted: list[str] = []
        for i in ids or []:
            if str(i) not in wanted:
                wanted.append(str(i))
        if not wanted:
            raise UserError("Pick at least one chat.", "Tick the chats TG Kit should follow.")
        try:
            limit = None if limit in (0, "0", "all", None) else max(1, int(limit))
        except (TypeError, ValueError):
            limit = 1000
        with self._job_lock:
            if self.adding:
                raise UserError(*COPYING)
            if self.step:
                raise UserError("A login is under way.", "Finish it or close the setup, then add chats.")
            old = [r for r in (self._job["rows"] if self._job and keep else []) if r["id"] not in wanted]
            names = {r["id"]: r["name"] for r in (self._job["rows"] if self._job else [])}
            rows = old + [{"id": i, "name": names.get(i, ""), "state": "waiting", "count": 0,
                           "message": None, "hint": None} for i in wanted]
            job = {"running": True, "rows": rows, "wanted": wanted, "error": None, "switch": {},
                   "loop": None, "task": None, "thread": None}
            self._job = job
        th = threading.Thread(target=self._run_job, args=(job, limit), daemon=True, name="tgkit-add")
        job["thread"] = th
        th.start()
        return self.add_state()

    def _row(self, job: dict, ident: str) -> dict | None:
        return next((r for r in job["rows"] if r["id"] == ident), None)

    def _run_job(self, job: dict, limit) -> None:
        from . import net  # noqa: PLC0415

        ctx = net.Ctx(self.home, Store(self.home), self._cfg())
        wanted = set(job["wanted"])

        def pick(dialogs, _query):
            listed = {str(d["id"]): d for d in dialogs}
            with self._job_lock:
                for ident in job["wanted"]:
                    row, d = self._row(job, ident), listed.get(ident)
                    if d is None:
                        row.update(state="error", message="Telegram did not list this chat.",
                                   hint="Open Add chats again to see your chats as they are now.")
                    elif d.get("added") and not d.get("partial"):
                        row.update(name=d["name"], state="done", already=True)
                    else:  # new, or a first copy cut short that Retry copies again
                        row["name"] = d["name"]
            return [d for d in dialogs
                    if str(d["id"]) in wanted and (not d.get("added") or d.get("partial"))]

        def progress(ev):
            with self._job_lock:
                if ev["type"] == "switched":
                    job["switch"] = ev
                elif ev["type"] == "plan":
                    for c in ev["chats"]:
                        row = self._row(job, c["id"])
                        if row is not None and not row["name"]:
                            row["name"] = c["name"]
                elif ev["type"] == "chat":
                    row = self._row(job, ev["id"])
                    if row is not None:
                        row.update(state=ev["state"], count=ev.get("count", row["count"]),
                                   message=ev.get("message"), hint=ev.get("hint"),
                                   partial=bool(ev.get("partial")))

        def unfinished(state, message, hint):
            with self._job_lock:
                for row in job["rows"]:
                    if row["id"] in wanted and row["state"] in ("waiting", "copying"):
                        row.update(state=state, message=message, hint=hint)

        loop = asyncio.new_event_loop()
        try:
            task = loop.create_task(net.add_chats(ctx, None, limit, pick, progress))
            job["loop"], job["task"] = loop, task
            with contextlib.redirect_stdout(io.StringIO()):
                loop.run_until_complete(task)
            unfinished("error", "This chat was not copied.", "Press Retry for this chat.")
        except asyncio.CancelledError:
            unfinished("stopped", "Stopped before this chat was copied.", "Press Retry for this chat.")
        except Exception as exc:  # noqa: BLE001
            err = exc if isinstance(exc, UserError) and "already running" not in exc.message else (
                UserError(*BUSY) if isinstance(exc, UserError) else _friendly(exc, self.home))
            job["error"] = {"message": err.message, "hint": err.hint}
            unfinished("error", err.message, err.hint)
        finally:
            with contextlib.suppress(Exception):
                loop.close()
            job["running"] = False

    def add_state(self) -> dict:
        job = self._job
        if not job:
            return {"running": False, "rows": [], "added": 0, "failed": 0, "error": None, "switched": False}
        with self._job_lock:
            rows = [dict(r) for r in job["rows"]]
            out = {"running": job["running"], "rows": rows,
                   # a copy cut short ("partial") is a failure with a Retry, never "added"
                   "added": sum(1 for r in rows if r["state"] == "done" and not r.get("already")
                                and not r.get("partial")),
                   "failed": sum(1 for r in rows if r["state"] in ("error", "stopped") or r.get("partial")),
                   "error": job["error"]}
            out.update(_switch_fields(job["switch"]))
        return out

    def stop(self, wait: float = 15.0) -> bool:
        """Cancel the copy of ticked chats (or end a login): its locks are freed.
        True when nothing of this window holds the sync lock any more."""
        job = self._job
        if job and job["running"]:
            loop, task = job.get("loop"), job.get("task")
            if loop is not None and task is not None:
                with contextlib.suppress(RuntimeError):
                    loop.call_soon_threadsafe(task.cancel)
            th = job.get("thread")
            if th is not None:
                th.join(wait)
        if self.step:
            self.cancel()
        return not self.adding and not self.step


def _switch_fields(res: dict | None) -> dict:
    """What a switch really moved, for the window (see account.switch_notes)."""
    res = res or {}
    return {"switched": bool(res.get("switched")), "parked": res.get("parked"),
            "comes_back": bool(res.get("comes_back")), "restored": bool(res.get("restored"))}
