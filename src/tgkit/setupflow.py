"""Setup inside the TG Kit window: Telegram codes, login, and picking chats.

The same steps as `tgkit setup`, one HTTP call per step, for people who never
open a terminal:

  codes(api_id, api_hash)  -> saved in config.json (checked first)
  phone(number)            -> Telegram sends a login code   -> "code"
  code(code)               -> logged in, or "password" (two-step verification)
  password(pw)             -> logged in
  dialogs()                -> the user's Telegram chats, with "added" flags
  add(ids, limit)          -> net.add_chats: the guarded add, under the sync lock

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
import sqlite3
import threading
import time
from pathlib import Path

from . import account, applog, config
from .errors import UserError
from .store import SessionLock, Store, SyncLock

LOGIN_TIMEOUT = 15 * 60  # an unfinished login is dropped after this many seconds


BUSY = ("TG Kit is still busy with Telegram (getting new messages or copying chats).",
        "Wait a moment and try again.")
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
    if isinstance(exc, (ConnectionError, TimeoutError, asyncio.TimeoutError, OSError)):
        return UserError("Could not reach Telegram.", "Check your internet connection and try again.")
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
            self._lock = SyncLock(self.home).acquire()  # no sync uses the login file meanwhile
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

        ctx = net.Ctx(self.home, Store(self.home), self._cfg())

        async def fetch():
            async with net.connected(ctx) as client:
                # Who the login file belongs to now: another account than the chats
                # shown (a login made elsewhere) switches to that account's chats.
                account.adopt(self.home, await net.session_me(client))
                found = await net.list_dialogs(client)
                existing = {d.name for d in ctx.store.chat_dirs()}
                keys = net.followed_keys(ctx.store)
                return [{"id": str(d["id"]), "name": d["name"], "kind": d["kind"],
                         "added": (net.folder_for(d["entity"], d["name"]) in existing
                                   or net.chat_key(net.entity_meta(d["entity"])) in keys)} for d in found]
        try:
            return {"dialogs": net.run(fetch())}
        except Exception as exc:  # noqa: BLE001
            raise _friendly(exc, self.home) from None

    def add(self, ids, limit) -> dict:
        from . import net  # noqa: PLC0415

        wanted = {str(i) for i in (ids or [])}
        if not wanted:
            raise UserError("Pick at least one chat.", "Tick the chats TG Kit should follow.")
        try:
            limit = None if limit in (0, "0", "all", None) else max(1, int(limit))
        except (TypeError, ValueError):
            limit = 1000
        ctx = net.Ctx(self.home, Store(self.home), self._cfg())
        before = len(ctx.store.chat_dirs())

        def pick(dialogs, _query):
            return [d for d in dialogs if str(d["id"]) in wanted and not d.get("added")]
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                net.run(net.add_chats(ctx, None, limit, pick))
        except Exception as exc:  # noqa: BLE001
            raise _friendly(exc, self.home) from None
        added = len(Store(self.home).chat_dirs()) - before
        lines = [x.strip() for x in out.getvalue().splitlines() if x.strip()]
        return {"added": added, "lines": lines}
