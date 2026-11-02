"""The local copy of your chats, on disk. Nothing here touches the network.

Layout of the data folder (see config.home()):

    chats/<folder>/messages.jsonl   one message per line, NEWEST first
    chats/<folder>/channel.json     title, id, type of the chat
    chats/<folder>/media/           attachments, original file names
    senders.json                    sender id -> display name
    sync-state.json                 when each chat was last checked
    tasks.json                      your task list
    config.json                     your api_id / api_hash and who you are

Rule kept from the original tool: readers tolerate a damaged line and warn;
the sync writer refuses (see net.newest_id).
"""

import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .errors import UserError

STALE_AFTER = timedelta(hours=12)


def atomic_write(path: Path, text: str) -> None:
    """The only way this package writes a file: tmp, flush, fsync, replace.

    A crash leaves either the old file or the new one, never half of each.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    tmp.replace(path)


def write_json(path: Path, data) -> None:
    atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


# ------------------------------------------------------------------ sync lock

LOCK_NAME = "sync.lock"
_LOCK_AT = 1 << 20  # the locked byte sits past the text, so the pid stays readable


def _try_lock(fd: int) -> bool:
    """Take the operating system's lock on the open lock file without waiting.

    The system frees it when the holder closes the file or its process ends, so
    a crashed sync never leaves a lock behind and no one ever has to guess that a
    lock is stale (guessing let two syncs hold it at once)."""
    if os.name == "nt":
        import msvcrt  # noqa: PLC0415

        os.lseek(fd, _LOCK_AT, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True
    import fcntl  # noqa: PLC0415

    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock(fd: int) -> None:
    if os.name == "nt":
        import msvcrt  # noqa: PLC0415

        os.lseek(fd, _LOCK_AT, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl  # noqa: PLC0415

        fcntl.flock(fd, fcntl.LOCK_UN)


class SyncLock:
    """One writer of chat data at a time per data folder (sync, add): two syncs at
    once would both read the same watermark and both prepend the same new
    messages (duplicate rows).

    `sync.lock` is opened (never moved or deleted, so every process locks the
    same file) and locked with the operating system's file lock, held until
    release. The system drops the lock of a process that dies, so there is no
    stale-lock takeover. The file holds the holder's pid and start time, for
    people only.
    """

    def __init__(self, home: Path):
        self.path = Path(home) / LOCK_NAME
        self.held = False
        self._fd: int | None = None

    def acquire(self) -> "SyncLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        if not _try_lock(fd):
            os.close(fd)
            raise UserError(
                "Another TG Kit sync is already running for this data folder.",
                "Wait for it to finish, then try again.",
            )
        body = json.dumps({"pid": os.getpid(),
                           "started": datetime.now(timezone.utc).replace(microsecond=0).isoformat()})
        try:
            os.ftruncate(fd, 0)
            os.lseek(fd, 0, os.SEEK_SET)
            os.write(fd, body.encode("utf-8"))
        except OSError:
            pass  # the text is for people; the lock is what counts
        self._fd = fd
        self.held = True
        return self

    def release(self) -> None:
        """Free only the lock this object holds; the file stays for the next sync."""
        if not self.held or self._fd is None:
            return
        fd, self._fd, self.held = self._fd, None, False
        try:
            os.ftruncate(fd, 0)
        except OSError:
            pass
        try:
            _unlock(fd)
        except OSError:
            pass
        finally:
            os.close(fd)

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *exc):
        self.release()
        return False


def read_json(path: Path, default):
    """Read a JSON file; a missing file gives `default`, a damaged one raises UserError."""
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        raise UserError(
            f"The file {path} could not be read ({type(exc).__name__}). Nothing was changed.",
            f"Rename or fix {path.name} and try again.",
        ) from None


# ------------------------------------------------------------------ text tools


def strip_accents(text: str) -> str:
    """'Biên Bản Đã' -> 'Bien Ban Da' (case kept, so regex escapes like \\D survive)."""
    text = unicodedata.normalize("NFD", text or "")
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return unicodedata.normalize("NFC", text.replace("đ", "d").replace("Đ", "D"))


def fold(text: str) -> str:
    """Lower case without Vietnamese accents: 'Biên Bản Đã' -> 'bien ban da'."""
    return strip_accents(text).casefold()


def has_accents(text: str) -> bool:
    return fold(text) != (text or "").casefold()


def matcher(query: str, case: bool = False):
    """A function text -> bool for what the user typed.

    Typed without accents, it matches with or without them ("bien ban" finds
    "Biên bản"). Typed with accents, accents must match. A pattern that is not
    a valid regular expression is searched as plain text instead of failing.
    """
    query = unicodedata.normalize("NFC", query or "")
    accentless = not has_accents(query)
    flags = 0 if case else re.IGNORECASE
    loose = accentless and not case
    pattern = strip_accents(query) if loose else query
    try:
        rx = re.compile(pattern, flags)
    except re.error:
        rx = re.compile(re.escape(pattern), flags)

    def test(text: str) -> bool:
        text = unicodedata.normalize("NFC", text or "")
        if loose:
            return bool(rx.search(strip_accents(text)))
        return bool(rx.search(text))

    return test


# ------------------------------------------------------------------- dates


def parse_date(value) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def local_time(value) -> str:
    dt = parse_date(value)
    if dt is None:
        return "????-??-?? ??:??"
    return dt.astimezone().strftime("%Y-%m-%d %H:%M")


def parse_since(text: str | None, default: str | None = None) -> datetime | None:
    """'7d', '24h', '2w', 'today', 'yesterday', '2026-09-01' or 'all' -> cutoff (UTC)."""
    text = (text or default or "").strip().lower()
    if not text or text == "all":
        return None
    now = datetime.now(timezone.utc)
    if text == "today":
        return datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    if text == "yesterday":
        start = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
        return start - timedelta(days=1)
    m = re.fullmatch(r"(\d+)\s*(h|hours?|d|days?|w|weeks?)", text)
    if m:
        n, unit = int(m.group(1)), m.group(2)[0]
        return now - {"h": timedelta(hours=n), "d": timedelta(days=n), "w": timedelta(weeks=n)}[unit]
    dt = parse_date(text)
    if dt is not None:
        return dt
    raise UserError(
        f"I did not understand the time '{text}'.",
        "Use something like 7d (7 days), 24h (24 hours), 2w (2 weeks), today, all, "
        "or a date like 2026-09-01.",
    )


def describe_since(text: str | None, default: str | None = None) -> str:
    text = (text or default or "all").strip().lower()
    m = re.fullmatch(r"(\d+)\s*(h|hours?|d|days?|w|weeks?)", text)
    if m:
        n, unit = int(m.group(1)), {"h": "hour", "d": "day", "w": "week"}[m.group(2)[0]]
        return f"last {n} {unit}{'s' if n != 1 else ''}"
    if text == "all":
        return "all messages"
    return f"since {text}"


# ------------------------------------------------------------------- store


class Store:
    def __init__(self, home: Path):
        self.home = Path(home)
        self.chats = self.home / "chats"
        self._senders: dict | None = None
        self._titles: dict = {}

    # chats ---------------------------------------------------------------

    def chat_dirs(self) -> list[Path]:
        if not self.chats.exists():
            return []
        return sorted(
            d for d in self.chats.iterdir()
            if d.is_dir() and not d.name.startswith(".") and (d / "messages.jsonl").exists()
        )

    def meta(self, d: Path) -> dict:
        path = d / "channel.json"
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8")) or {}
            except (json.JSONDecodeError, UnicodeDecodeError, OSError):
                pass
        return {}

    def title(self, d: Path) -> str:
        if d not in self._titles:
            meta = self.meta(d)
            name = meta.get("title") or " ".join(
                x for x in [meta.get("first_name"), meta.get("last_name")] if x
            )
            self._titles[d] = name or d.name.replace("_", " ")
        return self._titles[d]

    def match(self, pattern: str | None) -> list[Path]:
        """Chats whose folder name or title matches what the user typed.

        Raises UserError listing the known chats when nothing matches.
        """
        dirs = self.chat_dirs()
        if not dirs:
            raise UserError(
                "You have no chats in TG Kit yet.",
                "Run: tgkit setup   (or: tgkit add) to pick the chats to follow.",
            )
        if not pattern:
            return dirs
        test = matcher(pattern)
        hit = [d for d in dirs if test(d.name) or test(self.title(d))]
        if not hit:
            known = "\n".join(f"  - {self.title(d)}" for d in dirs)
            raise UserError(
                f"No chat matches '{pattern}'. Your chats are:\n{known}",
                "Type part of one of these names.",
            )
        return hit

    def rows(self, d: Path) -> list[dict]:
        """Messages for one chat, OLDEST first (the file is newest first).

        A damaged line is skipped but warned about, so an answer is never
        silently built from a truncated view.
        """
        out = []
        bad = 0
        path = d / "messages.jsonl"
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.strip():
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    bad += 1
                    if bad == 1:
                        print(f"WARNING: {self.title(d)} line {n} is damaged; "
                              f"results for this chat are incomplete.", file=sys.stderr)
        if bad > 1:
            print(f"WARNING: {self.title(d)}: {bad} damaged line(s) in total.", file=sys.stderr)
        out.reverse()
        return out

    # freshness -----------------------------------------------------------

    def sync_state(self) -> dict:
        try:
            return read_json(self.home / "sync-state.json", {})
        except UserError:
            return {}

    def synced_at(self, d: Path, state: dict | None = None) -> datetime | None:
        """When this chat's local copy was last brought up to date."""
        state = self.sync_state() if state is None else state
        times = []
        checked = parse_date(state.get(d.name))
        if checked:
            times.append(checked)
        try:
            times.append(datetime.fromtimestamp((d / "messages.jsonl").stat().st_mtime, timezone.utc))
        except OSError:
            pass
        return max(times) if times else None

    def is_stale(self, when: datetime | None) -> bool:
        return when is None or datetime.now(timezone.utc) - when > STALE_AFTER

    # people --------------------------------------------------------------

    def senders(self) -> dict:
        if self._senders is None:
            try:
                self._senders = read_json(self.home / "senders.json", {})
            except UserError:
                self._senders = {}
        return self._senders

    def sender_name(self, sid) -> str:
        if sid is None:
            return "(unknown)"
        return self.senders().get(str(sid)) or f"user {sid}"

    def merge_senders(self, names: dict) -> None:
        """Add newly seen names to senders.json. Never overwrites a damaged file."""
        if not names:
            return
        path = self.home / "senders.json"
        try:
            known = read_json(path, {})
        except UserError:
            print(f"WARNING: {path.name} is damaged; new names were not saved.", file=sys.stderr)
            return
        new = {k: v for k, v in names.items() if v and known.get(k) != v}
        if new:
            known.update(new)
            write_json(path, known)
        self._senders = known

    # display -------------------------------------------------------------

    def fmt(self, r: dict, chat: str | None = None, width: int = 240) -> str:
        m = r.get("media") or {}
        tag = m.get("file_name") or ("photo" if m.get("kind") == "photo" else m.get("kind")) or ""
        text = (r.get("text") or "").replace("\n", " / ")
        if width and len(text) > width:
            text = text[:width] + "..."
        where = f" ({chat})" if chat else ""
        line = f"[{local_time(r.get('date'))}] {self.sender_name(r.get('sender_id'))}{where}: "
        line += text or "(no text)"
        if tag:
            line += f"  [file: {tag}]"
        extra = f"#{r.get('id')}"
        if r.get("reply_to_msg_id"):
            extra += f", reply to #{r['reply_to_msg_id']}"
        return line + f"   ({extra})"


def thread_of(rs: list[dict], anchor: int) -> list[dict]:
    """Every message reachable from `anchor` by reply links, in time order.

    Walks up via reply_to_msg_id to the root, then collects the whole subtree
    plus anything sharing an album (grouped_id) with a member.
    """
    by_id = {r["id"]: r for r in rs if r.get("id") is not None}
    if anchor not in by_id:
        return []
    root = thread_root(by_id, anchor)
    keep = {root}
    changed = True
    while changed:
        changed = False
        for r in rs:
            rid, par = r.get("id"), r.get("reply_to_msg_id")
            if rid not in keep and par in keep:
                keep.add(rid)
                changed = True
    groups = {r.get("grouped_id") for r in rs if r.get("id") in keep and r.get("grouped_id")}
    if groups:
        for r in rs:
            if r.get("grouped_id") in groups and r.get("id") is not None:
                keep.add(r["id"])
    return [r for r in rs if r.get("id") in keep]


def thread_root(by_id: dict, anchor: int) -> int:
    root = anchor
    seen = set()
    while True:
        parent = by_id.get(root, {}).get("reply_to_msg_id")
        if not parent or parent not in by_id or parent in seen:
            return root
        seen.add(parent)
        root = parent
