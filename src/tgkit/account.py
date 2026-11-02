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

import contextlib
import json
import os
import time
from datetime import datetime
from pathlib import Path

from . import config
from .errors import UserError
from .store import Store, SyncLock, atomic_write

# What belongs to one Telegram account; moved together by "backup". usage.jsonl
# is not here: token use belongs to the Claude login on this computer, and the
# daily limit must keep counting across a Telegram account switch.
ACCOUNT_DATA = ("chats", "tasks.json", "senders.json", "sync-state.json", "summary.json",
                "app/conversations")
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


OWNER_FILE = "account.json"  # whose chats are in the data folder now: {id, name, username}
ACCOUNTS_DIR = "accounts"  # the chats of other accounts, one folder per Telegram account id


def owner(base: Path) -> dict | None:
    """The account the chats shown belong to: account.json, else "me" in config.json."""
    base = Path(base)
    try:
        data = json.loads((base / OWNER_FILE).read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("id") is not None:
            return data
    except (OSError, ValueError):
        pass
    me = config.load(base).get("me") if config.config_path(base).exists() else None
    return me if isinstance(me, dict) and me.get("id") is not None else None


def _move_set(src: Path, dst: Path) -> list[str]:
    """Move one account's data from src to dst. Never overwrites: a name already at
    dst stops the move before anything moved."""
    items = [i for i in ACCOUNT_DATA if (src / i).exists()]
    clash = [i for i in items if (dst / i).exists()]
    if clash:
        raise UserError(f"TG Kit could not switch accounts: {dst / clash[0]} is already there.",
                        "Move it away, then try again.")
    moved: list[str] = []
    try:
        for i in items:
            target = dst / i
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(src / i, target)
            moved.append(i)
    except OSError as exc:
        # A file in use (Windows) or a full disk: put back what already moved, so
        # one account's data is never split across two places, and say so plainly.
        for i in reversed(moved):
            with contextlib.suppress(OSError):
                os.replace(dst / i, src / i)
        raise UserError(f"TG Kit could not move {src / (items[len(moved)])} "
                        f"({exc.strerror or type(exc).__name__}). Nothing was deleted.",
                        "Close any program that has a file of TG Kit open, then try again.") from None
    return items


def switch(base: Path, me: dict) -> dict:
    """Show the chats of `me`. The chats of the account shown until now move to
    accounts/<its id>/, and the chats `me` had here before come back from
    accounts/<me id>/. Nothing is deleted. The caller holds the sync lock."""
    base = Path(base)
    old = owner(base)
    # switched / parked / restored say only what really moved: a message built on
    # them (switch_notes) never claims a move that did not happen.
    res = {"switched": False, "parked": None, "comes_back": False, "restored": False}
    if old and str(old.get("id")) != str(me["id"]):
        park = base / ACCOUNTS_DIR / str(old["id"])
        if any((park / i).exists() for i in ACCOUNT_DATA):
            park = park.with_name(f"{old['id']}-{datetime.now():%Y%m%d-%H%M%S}")
        comes_back = park.name == str(old["id"])  # a dated folder is not brought back by a login
        if _move_set(base, park):
            atomic_write(park / "READ ME.txt",
                         f"TG Kit data of {old.get('name') or 'an earlier Telegram account'}, moved here on "
                         f"{datetime.now():%Y-%m-%d %H:%M} when this computer logged in to another account.\n"
                         + ("Nothing was deleted. Log in to that account again and TG Kit brings it back.\n"
                            if comes_back else
                            "Nothing was deleted. To use it again, log in to that account, close TG Kit and\n"
                            "move these folders and files back into the data folder.\n"))
            res["parked"] = str(park)
            res["comes_back"] = comes_back
        back = base / ACCOUNTS_DIR / str(me["id"])
        if back.is_dir():
            res["restored"] = bool(_move_set(back, base))
            with contextlib.suppress(OSError):
                (back / "READ ME.txt").unlink()
                back.rmdir()
        res["switched"] = bool(res["parked"] or res["restored"])
    atomic_write(base / OWNER_FILE, json.dumps(me, ensure_ascii=False))
    config.put(base, me=me)
    return res


def switch_notes(res: dict) -> list[str]:
    """Plain lines for what a switch really moved; none when nothing moved."""
    notes = []
    if res.get("parked") and res.get("comes_back"):
        notes.append("This is another Telegram account: the chats of the earlier one were moved aside "
                     "and come back when you log in to it again.")
    elif res.get("parked"):
        notes.append(f"This is another Telegram account: the chats of the earlier one were moved to "
                     f"{res['parked']}. Nothing was deleted.")
    if res.get("restored"):
        notes.append("The chats this account had here before are back.")
    return notes


def adopt(base: Path, me: dict | None, locked: bool = False) -> dict:
    """After a login, or when a Telegram call shows who the login file belongs to:
    save who it is and, for another account than the chats shown, switch."""
    if not me or me.get("id") is None:
        return {"switched": False}
    base = Path(base)
    cfg_me = config.load(base).get("me") if config.config_path(base).exists() else None
    old = owner(base)
    if old and str(old.get("id")) == str(me["id"]) and cfg_me == me and (base / OWNER_FILE).exists():
        return {"switched": False}
    if locked:
        return switch(base, me)
    with SyncLock(base):
        return switch(base, me)


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
        if data == "keep":
            # The kept chats still belong to the account logged out: write it down
            # before "me" is forgotten (a folder from 0.2.3 has no account.json), so
            # a login to another account moves them aside instead of showing them as its own.
            was = owner(base)
            if was and not (base / OWNER_FILE).exists():
                atomic_write(base / OWNER_FILE, json.dumps(was, ensure_ascii=False))
        if config.config_path(base).exists():
            config.put(base, me=None)  # under the config lock: a settings change meanwhile stays
        if data == "backup":
            dest = _backup(base, name)
            result["backup"] = str(dest) if dest else None
    return result
