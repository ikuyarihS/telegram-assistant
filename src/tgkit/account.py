"""Log out of Telegram on this computer, to stop or to switch to another account.

What a log out does, in order, under the sync lock (no sync or add can run
meanwhile):

  1. asks Telegram to end this computer's login (if Telegram can be reached);
  2. removes the login file (tgkit.session) either way, so the next
     `tgkit setup` logs in fresh, with any account;
  3. forgets who was logged in ("me" in config.json). api_id / api_hash stay:
     they belong to TG Kit, not to an account, and work with any account;
  4. the copied chats stay, unless the user chose "move to a backup folder":
     then chats, tasks and the app's conversations move into
     <data>/old-account-<date>/. Nothing is ever deleted.
"""

import os
import time
from datetime import datetime
from pathlib import Path

from . import config
from .errors import UserError
from .store import Store, SyncLock, atomic_write

# What belongs to one Telegram account; moved together by "backup".
ACCOUNT_DATA = ("chats", "tasks.json", "senders.json", "sync-state.json", "summary.json",
                "app/conversations", "usage.jsonl")
SESSION_FILES = ("tgkit.session", "tgkit.session-journal")


def who(base: Path) -> str | None:
    me = config.load(base).get("me") if config.config_path(base).exists() else None
    if isinstance(me, dict):
        return me.get("name") or (f"@{me['username']}" if me.get("username") else None) or "your account"
    return None


def has_session(base: Path) -> bool:
    return any((Path(base) / n).exists() for n in SESSION_FILES)


def data_summary(base: Path) -> dict:
    store = Store(base)
    return {"chats": len(store.chat_dirs()), "tasks": (Path(base) / "tasks.json").exists()}


def _remove_session(base: Path) -> list[str]:
    """Remove the login file. Retried briefly: Windows may hold it a moment after close."""
    gone = []
    for name in SESSION_FILES:
        p = Path(base) / name
        for _ in range(20):
            try:
                p.unlink()
                gone.append(name)
                break
            except FileNotFoundError:
                break
            except PermissionError:
                time.sleep(0.1)
        else:
            raise UserError(f"The login file {p} is in use and could not be removed.",
                            "Close every TG Kit window and terminal, then log out again.")
    return gone


def _backup(base: Path, name: str | None) -> Path | None:
    items = [i for i in ACCOUNT_DATA if (Path(base) / i).exists()]
    if not items:
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = Path(base) / f"old-account-{stamp}"
    n = 2
    while dest.exists():
        dest = Path(base) / f"old-account-{stamp}-{n}"
        n += 1
    dest.mkdir(parents=True)
    for i in items:
        target = dest / i
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(Path(base) / i, target)
    atomic_write(dest / "READ ME.txt",
                 f"TG Kit data of {name or 'an earlier Telegram account'}, moved here on "
                 f"{datetime.now():%Y-%m-%d %H:%M} when you logged out.\n"
                 "Nothing was deleted. To use it again, log in to that account, close TG Kit and\n"
                 "move these folders and files back into the data folder.\n")
    return dest


def logout(base: Path, data: str = "keep", server=None) -> dict:
    """Log this computer out. `data` is "keep" or "backup". `server(ctx)` is the
    Telegram call (net.logout; tests pass a stand-in)."""
    if data not in ("keep", "backup"):
        raise UserError(f"'{data}' is not a choice for your chats.", "Choose keep or backup.")
    base = Path(base)
    name = who(base)
    result = {"was": name, "telegram": False, "telegram_note": None, "removed": [], "backup": None}
    with SyncLock(base):
        cfg = config.load(base) if config.config_path(base).exists() else {}
        if has_session(base) and config.has_credentials(base, cfg):
            try:
                if server is None:
                    from . import net  # noqa: PLC0415

                    result["telegram"] = bool(net.run(net.logout(net.Ctx(base, Store(base), cfg))))
                else:
                    result["telegram"] = bool(server(base, cfg))
            except UserError as exc:
                result["telegram_note"] = exc.message
            except Exception as exc:  # noqa: BLE001  (offline, revoked, ...: the local log out still happens)
                result["telegram_note"] = f"Telegram could not be reached ({type(exc).__name__})."
        result["removed"] = _remove_session(base)
        if cfg.pop("me", None) is not None:
            config.save(base, cfg)
        if data == "backup":
            dest = _backup(base, name)
            result["backup"] = str(dest) if dest else None
    return result
