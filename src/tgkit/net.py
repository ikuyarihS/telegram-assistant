"""Everything that talks to Telegram: sync, add a chat, online search, names, login.

Ported from the original single-user tool with its sync semantics UNCHANGED.
Read docs/MAINTENANCE.md before changing anything here. The short version:

  * sync only fetches messages newer than the newest id already on disk; it
    never rewrites history and never re-downloads a file already on disk (a
    file recorded on its row as pending or failed is fetched again);
  * text first: a chat's messages are written as soon as its text is read; its
    files download after, in the background (MediaPool), never blocking the
    next chat. A few chats are read at once on the ONE client of the process;
  * a chat folder with zero rows is SKIPPED, never crawled from the start
    ("never crawled" is not "sync everything");
  * a damaged line in messages.jsonl aborts sync for that chat before any
    connection: a watermark computed from a partly readable file is not one;
  * iter_messages returns NEWEST FIRST, so a fetch that dies partway holds the
    newest N and leaves a hole above the watermark. Such a partial fetch is
    DISCARDED and the watermark held. Never relax this into committing it.
"""

import asyncio
import contextlib
import inspect
import json
import os
import re
import shutil
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import account, applog, config
from .errors import UserError
from .store import SessionLock, Store, SyncLock, atomic_write, write_json


class _NoFloodWait(Exception):
    seconds = 0


def _flood_error():
    try:
        from telethon.errors import FloodWaitError  # noqa: PLC0415
    except ImportError:
        return _NoFloodWait
    return FloodWaitError


CHATS_AT_ONCE = 3      # chats read at the same time, on the one client of the process (R14)
FILES_AT_ONCE = 3      # attachments downloaded at the same time, after the text
FLOOD_MAX_WAIT = 300   # seconds: a longer FloodWait ends that chat with a plain message
RETRY_FILES_MAX = 200  # files of one chat a sync tries again (pending or failed earlier)
MEDIA_TRIES = 3        # a file that failed this many times is not tried again
_sleep = asyncio.sleep  # the tests replace it to wait out a FloodWait at once


class Stopped(Exception):
    """The person pressed Stop: end cleanly, keep what is saved."""


class Ctx:
    """What a network command needs: the data folder, its store and config.

    `media` is the Files choice (config.MEDIA_MODES), `stop()` says Stop was pressed."""

    def __init__(self, base: Path, store: Store, cfg: dict, media: str | None = None, stop=None):
        self.base = Path(base)
        self.store = store
        self.cfg = cfg
        self.max_media = config.max_media_bytes(self.base, cfg)
        self.media = media if media in config.MEDIA_MODES else config.media_mode(cfg)
        self.stop = stop or (lambda: False)

    @property
    def cap(self) -> int:
        """The largest file to download now (0: text only)."""
        if self.media == "none":
            return 0
        return 1 << 62 if self.media == "all" else self.max_media


def make_client(base: Path, cfg: dict):
    api_id, api_hash = config.credentials(base, cfg)
    try:
        from telethon import TelegramClient  # noqa: PLC0415
    except ImportError:
        raise UserError(
            "The Telegram part of Telegram Assistant is missing on this computer.",
            "Run the installer again (see README).",
        ) from None
    base.mkdir(parents=True, exist_ok=True)
    return TelegramClient(str(config.session_base(base)), api_id, api_hash)


async def _maybe_await(value):
    if inspect.isawaitable(value):
        await value


@contextlib.asynccontextmanager
async def connected(ctx: Ctx, need_login: bool = True):
    """A connected client. Refuses (never prompts) when this computer is not logged in.
    Made under the session lock: one client on tgkit.session at a time."""
    with SessionLock(ctx.base):
        client = make_client(ctx.base, ctx.cfg)
        await client.connect()
        try:
            if need_login and not await client.is_user_authorized():
                raise UserError(
                    "You are not logged in to Telegram on this computer yet.",
                    "Run: tgkit setup",
                )
            yield client
        finally:
            await _maybe_await(client.disconnect())


async def session_me(client) -> dict | None:
    """Who the login file belongs to: {id, name, username}, or None when unknown."""
    get = getattr(client, "get_me", None)
    if get is None:
        return None
    me = await get()
    ident = getattr(me, "id", None)
    if not isinstance(ident, int):
        return None
    return {"id": ident, "name": display_name(me), "username": getattr(me, "username", None)}


def run(coro) -> int:
    """Run a network coroutine, turning connection trouble into plain words."""
    try:
        return asyncio.run(coro)
    except (ConnectionError, TimeoutError, asyncio.TimeoutError, socket.gaierror):
        raise UserError(
            "Could not reach Telegram.",
            "Check your internet connection and try again.",
        ) from None


# ------------------------------------------------------------- message shape


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", text).strip("_") or "channel"


def as_target(name: str):
    """Numeric ids must be ints for Telethon; @usernames and links stay strings."""
    try:
        return int(name)
    except ValueError:
        return name


def display_name(ent) -> str | None:
    if ent is None:
        return None
    name = " ".join(x for x in [getattr(ent, "first_name", None), getattr(ent, "last_name", None)] if x)
    return name or getattr(ent, "title", None) or getattr(ent, "username", None)


def entity_meta(entity) -> dict:
    kind = type(entity).__name__
    meta = {
        "id": entity.id,
        "type": kind,
        "title": getattr(entity, "title", None),
        "username": getattr(entity, "username", None),
    }
    if kind in ("Channel", "Chat"):
        meta["participants_count"] = getattr(entity, "participants_count", None)
        meta["broadcast"] = getattr(entity, "broadcast", None)
        meta["megagroup"] = getattr(entity, "megagroup", None)
    if kind == "User":
        meta["first_name"] = getattr(entity, "first_name", None)
        meta["last_name"] = getattr(entity, "last_name", None)
    return meta


def media_summary(msg) -> dict | None:
    """Describe the attachment without downloading it."""
    media = getattr(msg, "media", None)
    if not media:
        return None
    kind = type(media).__name__
    if kind == "MessageMediaWebPage":
        return None
    info: dict = {"kind": kind}
    if kind == "MessageMediaPhoto":
        info["kind"] = "photo"
    elif kind == "MessageMediaDocument":
        doc = media.document
        info["mime_type"] = getattr(doc, "mime_type", None)
        info["size"] = getattr(doc, "size", None)
        info["file_name"] = next(
            (a.file_name for a in (getattr(doc, "attributes", None) or []) if hasattr(a, "file_name")),
            None,
        )
    return info


def message_row(msg, media) -> dict:
    return {
        "id": msg.id,
        "date": msg.date.astimezone(timezone.utc).isoformat() if msg.date else None,
        "text": msg.message or "",
        "views": getattr(msg, "views", None),
        "forwards": getattr(msg, "forwards", None),
        "reply_to_msg_id": msg.reply_to_msg_id,
        "sender_id": getattr(msg.sender_id, "user_id", msg.sender_id),
        "grouped_id": msg.grouped_id,
        "media": media,
        "media_path": None,
    }


def remember_sender(names: dict, msg) -> None:
    sid = getattr(msg, "sender_id", None)
    name = display_name(getattr(msg, "sender", None))
    if sid is not None and name:
        names[str(getattr(sid, "user_id", sid))] = name


async def _flood_wait(seconds, ctx: "Ctx", beat=None) -> None:
    """Wait as long as Telegram asked, beating the lock every second; Stop ends the wait."""
    left = int(seconds or 0) + 1
    while left > 0:
        if ctx.stop():
            raise Stopped()
        if beat is not None:
            beat()
        await _sleep(1)
        left -= 1


# The fields of a row that say what happened to its attachment.
MEDIA_KEYS = ("media_path", "media_pending", "media_skipped", "media_error", "media_tries")


def want_file(ctx: "Ctx", row: dict) -> bool:
    """Decide, by the Files choice, whether this row's attachment is downloaded later.
    Every attachment not downloaded says why on its row, so none is lost silently."""
    media = row.get("media")
    if not media or row.get("media_path"):
        return False
    size = media.get("size") or 0
    if ctx.media == "none":
        row["media_skipped"] = "text only"
        row.pop("media_pending", None)
        return False
    if size and size > ctx.cap:
        row["media_skipped"] = f"size {size} > cap {ctx.cap}"
        row.pop("media_pending", None)
        return False
    row.pop("media_skipped", None)
    row["media_pending"] = True
    return True


async def _download(client, msg, row, media_dir: Path, rel_folder: str, flood, beat=None,
                    ctx: "Ctx | None" = None) -> int:
    """Download one attachment; failures are recorded on the row, never fatal.
    `beat()` runs for every chunk received: a big file is progress, not a hang.
    A FloodWait is waited out once (stoppable); a file still not fetched keeps a
    `media_error` and a later sync tries it again (up to MEDIA_TRIES times)."""
    ctx = ctx or Ctx.__new__(Ctx)
    stop = getattr(ctx, "stop", None) or (lambda: False)
    media_dir.mkdir(parents=True, exist_ok=True)
    kw = {"progress_callback": lambda _got, _total: beat()} if beat is not None else {}
    for attempt in range(2):
        if stop():
            raise Stopped()
        try:
            path = await client.download_media(msg, file=str(media_dir), **kw)
        except flood as exc:
            if attempt or (getattr(exc, "seconds", 0) or 0) > FLOOD_MAX_WAIT:
                row["media_error"] = "Telegram asked to wait; try again later"
                break
            await _flood_wait(exc.seconds, ctx, beat)
            continue
        except Stopped:
            raise
        except Exception as exc:  # noqa: BLE001
            row["media_error"] = f"{type(exc).__name__}: {exc}"
            break
        if path:
            row["media_path"] = str(Path(rel_folder) / "media" / Path(path).name)
            for key in ("media_pending", "media_error", "media_tries", "media_skipped"):
                row.pop(key, None)
            return 1
        row["media_error"] = "Telegram sent no file"
        break
    row.pop("media_pending", None)
    row["media_tries"] = int(row.get("media_tries") or 0) + 1
    return 0


def apply_media(d: Path, updates: dict) -> None:
    """Write what happened to the attachments of `updates` (id -> row) into the chat's
    messages.jsonl, whole file at once (R1). Only the MEDIA_KEYS of those rows change;
    every other line is kept byte for byte."""
    if not updates:
        return
    path = d / "messages.jsonl"
    if not path.exists():
        return
    out = []
    for line in path.read_text(encoding="utf-8").splitlines(keepends=True):
        text = line.strip()
        if text:
            try:
                r = json.loads(text)
            except json.JSONDecodeError:
                out.append(line)
                continue
            u = updates.get(r.get("id"))
            if u is not None:
                for key in MEDIA_KEYS:
                    if key in u:
                        r[key] = u[key]
                    elif key != "media_path":
                        r.pop(key, None)
                line = json.dumps(r, ensure_ascii=False) + "\n"
        out.append(line)
    atomic_write(path, "".join(out))


class MediaPool:
    """Attachments download here, after the text, FILES_AT_ONCE at a time: a slow file
    never holds up the next chat. Each chat's rows are written back (apply_media) when
    its files are done and every 20 files; a file not fetched keeps `media_pending`
    on disk (it was written so with the text), so a later sync tries it again."""

    def __init__(self, client, ctx: "Ctx", flood, beat=None):
        self.client, self.ctx, self.flood, self.beat = client, ctx, flood, beat
        self.q: asyncio.Queue = asyncio.Queue()
        self.chats: dict = {}
        self.total = 0
        self.got = 0
        self.workers = [asyncio.ensure_future(self._work()) for _ in range(FILES_AT_ONCE)]

    def add(self, d: Path, jobs: list, on_change=None) -> None:
        if not jobs:
            return
        st = self.chats.setdefault(d, {"left": 0, "got": 0, "skipped": 0, "rows": {}, "on": on_change})
        st["on"] = on_change or st["on"]
        for msg, row in jobs:
            st["left"] += 1
            st["rows"][row["id"]] = row
            self.total += 1
            self.q.put_nowait((d, msg, row))

    def left(self, d: Path) -> int:
        st = self.chats.get(d)
        return st["left"] if st else 0

    def _flush(self, d: Path) -> None:
        st = self.chats[d]
        rows, st["rows"] = st["rows"], {}
        try:
            apply_media(d, rows)
        except Exception as exc:  # noqa: BLE001  (the rows stay "pending" on disk: tried again later)
            applog.caught("files: the chat's rows were not updated", exc, self.ctx.base)

    async def _work(self) -> None:
        while True:
            d, msg, row = await self.q.get()
            st = self.chats[d]
            try:
                if self.ctx.stop():
                    st["skipped"] += 1  # left for the next sync: the row stays media_pending
                else:
                    got = await _download(self.client, msg, row, d / "media", d.name, self.flood,
                                          self.beat, self.ctx)
                    st["got"] += got
                    self.got += got
            except Stopped:
                st["skipped"] += 1
            except asyncio.CancelledError:
                st["skipped"] += 1  # Stop cancelled it mid-download: the next sync gets it
                raise
            except Exception as exc:  # noqa: BLE001
                applog.caught("files: a download failed", exc, self.ctx.base)
            finally:
                st["left"] -= 1
                if st["left"] == 0 or st["got"] % 20 == 0:
                    self._flush(d)
                if st["on"] is not None:
                    with contextlib.suppress(Exception):
                        st["on"](st["left"], st["got"], st["skipped"])
                self.q.task_done()

    async def finish(self) -> None:
        """Wait for every queued file (Stop makes the rest end at once), then write back."""
        try:
            await self.q.join()
        finally:
            await self.close()

    async def close(self) -> None:
        """End the workers and write back what is known. Safe to run twice; runs on
        every way out (a cancelled copy too: its rows stay "pending" on disk)."""
        for w in self.workers:
            w.cancel()
        with contextlib.suppress(BaseException):
            await asyncio.gather(*self.workers, return_exceptions=True)
        for d, st in self.chats.items():
            if st["rows"]:
                self._flush(d)


async def read_text(client, ctx: "Ctx", entity, rows: list, names: dict, flood, beat=None, on_count=None,
                    min_id: int = 0, limit: int | None = None, msgs: dict | None = None):
    """Read a chat's messages NEWEST FIRST into `rows` (no file is downloaded here).
    Returns None when the read finished, else what stopped it (Stopped, or the error).
    A FloodWait is waited out (stoppable, at most FLOOD_MAX_WAIT seconds, 3 times) and
    the read goes on from the oldest message read so far, so the rows stay contiguous.
    `msgs` collects the messages whose attachment is wanted (id -> message)."""
    waits = 0
    while True:
        kw: dict = {}
        if min_id:
            kw["min_id"] = min_id
        if rows:
            kw["offset_id"] = rows[-1]["id"]  # older than the oldest read so far
        if limit is not None:
            if limit - len(rows) <= 0:
                return None
            kw["limit"] = limit - len(rows)
        try:
            async for msg in client.iter_messages(entity, **kw):
                if ctx.stop():
                    return Stopped()
                row = message_row(msg, media_summary(msg))
                remember_sender(names, msg)
                if beat is not None:
                    beat()
                if msgs is not None and want_file(ctx, row):
                    msgs[row["id"]] = msg
                rows.append(row)
                if on_count is not None:
                    on_count(len(rows))
            return None
        except flood as exc:
            seconds = getattr(exc, "seconds", 0) or 0
            if waits >= 3 or seconds > FLOOD_MAX_WAIT:
                return exc
            waits += 1
            print(f"  Telegram asked to wait {seconds}s; waiting", flush=True)
            try:
                await _flood_wait(seconds, ctx, beat)
            except Stopped as stop:
                return stop
        except Exception as exc:  # noqa: BLE001
            return exc


# ---------------------------------------------------------------------- sync


def newest_id(d: Path) -> int:
    """Highest message id already on disk (0 if the chat has no rows).

    A damaged line is FATAL here, not skipped: skipping it lowers the
    watermark, sync re-fetches messages already present, and the prepend
    writes duplicate ids on top of an unreadable orphan every reader drops.
    """
    best = 0
    for n, line in enumerate((d / "messages.jsonl").read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            best = max(best, json.loads(line).get("id") or 0)
        except json.JSONDecodeError as exc:
            raise UserError(
                f"{d.name}/messages.jsonl line {n} is damaged ({exc}). "
                f"Refusing to sync this chat: sync would duplicate messages on top of it.",
                "Repair or remove that line, then run tgkit sync again.",
            ) from exc
    return best


def target_for(d: Path):
    """The address to re-fetch this chat with, from channel.json.

    channel.json stores the UNSIGNED id; supergroups and channels need the
    -100 prefix and small groups a plain negative. @username wins when set.
    """
    meta_path = d / "channel.json"
    if not meta_path.exists():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if meta.get("username"):
        return meta["username"]
    ident = meta.get("id")
    if ident is None:
        return None
    kind = meta.get("type")
    if kind == "User":
        return ident
    if kind == "Channel":
        return int(f"-100{ident}")
    return -ident


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


async def sync_chats(ctx: Ctx, dirs: list[Path], quiet: bool = False, dry_run: bool = False) -> int:
    """One sync per data folder at a time (store.SyncLock): two syncs reading the
    same watermark would both prepend the same new messages. A dry run writes
    nothing, so it needs no lock."""
    if dry_run:
        return await _sync_chats(ctx, dirs, quiet, dry_run)
    with SyncLock(ctx.store.home, "sync") as lock:
        return await _sync_chats(ctx, dirs, quiet, dry_run, lock)


SWITCHED_LINE = "Switched accounts:"  # the window reads this line (appserver.App.sync)


def other_account(owner: dict | None, me: dict | None) -> bool:
    """Is this computer logged in to another Telegram account than the one whose chats
    are shown? The one check sync, add, the window and search --online all use."""
    return bool(owner and me and str(owner.get("id")) != str(me["id"]))


async def _sync_chats(ctx: Ctx, dirs: list[Path], quiet: bool, dry_run: bool, lock=None) -> int:
    store = ctx.store

    def beat(what=None):  # the heartbeat the window reads to tell working from hung
        if lock is not None:
            lock.beat(what)
    # Every watermark is read BEFORE connecting: a damaged file aborts with a
    # clear message, not after a login round-trip.
    baseline = {d: newest_id(d) for d in dirs}
    flood = _flood_error()

    tally = {"added": 0, "failed": 0, "stopped": 0}
    names: dict = {}
    checked: dict = {}
    async with connected(ctx) as client:
        # The chats shown belong to one account: never copy another account's
        # messages into them (a login made elsewhere, the old "me" kept). The sync
        # switches to the logged-in account's chats (under its own sync lock) and
        # gets theirs instead; the other account's chats are moved aside, kept.
        owner, me = account.owner(ctx.base), await session_me(client)
        if other_account(owner, me):
            if dry_run:
                print("This computer is logged in to another Telegram account; a real sync "
                      "switches to that account's chats first.")
                return 0
            res = account.adopt(ctx.base, me, locked=True)
            ctx.cfg = config.load(ctx.base)
            print(f"{SWITCHED_LINE} this computer is logged in to {me.get('name') or 'another Telegram account'}, "
                  "so Telegram Assistant now shows that account's chats.", flush=True)
            for note in account.switch_notes(res):
                print(note, flush=True)
            dirs = store.chat_dirs()
            baseline = {d: newest_id(d) for d in dirs}
        # Private chats resolve by numeric id ONLY after the dialog cache is
        # filled; without it a cold session skips nearly everything and still
        # prints "0 new", a false clean.
        await client.get_dialogs()
        pool = None if dry_run else MediaPool(client, ctx, flood, beat)
        sem = asyncio.Semaphore(CHATS_AT_ONCE)

        async def one(d: Path) -> None:
            async with sem:
                if ctx.stop():
                    tally["stopped"] += 1
                    return
                await sync_one(d)

        async def sync_one(d: Path) -> None:
            title = store.title(d)
            beat(f"sync: {title}")
            target = target_for(d)
            if target is None:
                print(f"Skipped {title}: no usable address in channel.json", file=sys.stderr)
                return

            after = baseline[d]
            if after == 0:
                # A folder with no rows is "never fetched", NOT "fetch from the
                # beginning": min_id=0 would pull a chat's whole history.
                print(f"Skipped {title}: it has no messages on disk yet. sync only fetches "
                      f"what is new; add a chat with: tgkit add", file=sys.stderr)
                return

            try:
                entity = await client.get_entity(target)
            except Exception as exc:  # noqa: BLE001
                if (store.meta(d).get("from_crawl") or {}).get("account") == "not_seen":
                    # imported from tg-crawl, and this account never had it: kept, not a failure
                    print(f"Skipped {title}: it came from tg-crawl and your Telegram account does not have it.",
                          file=sys.stderr)
                    return
                applog.caught("sync: a chat could not be opened", exc, ctx.base)
                print(f"Skipped {title}: Telegram could not open this chat. Check that it still exists "
                      "and that you are a member.", file=sys.stderr)
                tally["failed"] += 1
                return

            fresh: list[dict] = []
            wanted: dict = {}
            failure = await read_text(client, ctx, entity, fresh, names, flood, lambda *_: beat(),
                                      min_id=after, msgs=None if dry_run else wanted)
            if isinstance(failure, Stopped):
                tally["stopped"] += 1
                print(f"  {title}: stopped", file=sys.stderr)
            elif failure is not None and isinstance(failure, flood):
                print(f"  {title}: Telegram asked to wait {failure.seconds}s; stopping this chat",
                      file=sys.stderr)
            elif failure is not None:
                applog.caught("sync: fetching new messages failed", failure, ctx.base)
                print(f"  {title}: getting new messages stopped with an error (details in the log file).",
                      file=sys.stderr)

            if dry_run:
                print(f"  {title}: would fetch {len(fresh)} message(s) after #{after}")
                return

            # A partial fetch is only safe to commit when it is CONTIGUOUS with
            # the watermark. iter_messages returns NEWEST FIRST, so a fetch that
            # dies partway holds the newest N and leaves a hole above `after`.
            # Writing it would move the watermark past messages never fetched:
            # permanent loss. Media duplicates are recoverable; lost messages
            # are not. Ids in one chat are sparse (a per-user global counter),
            # so this fires on nearly every partial fetch: a visible stall in
            # the safe direction, recovered by the next clean run.
            if fresh and failure is not None:
                lowest = min(r["id"] for r in fresh)
                if lowest > after + 1:
                    print(
                        f"  {title}: discarding {len(fresh)} message(s): the fetch stopped at "
                        f"#{lowest}, leaving a gap above #{after}. Nothing was lost; the next "
                        f"tgkit sync fetches the whole range again.",
                        file=sys.stderr,
                    )
                    fresh = []

            stopped = isinstance(failure, Stopped)
            if not fresh:
                if failure is not None:
                    tally["failed"] += 0 if stopped else 1
                else:
                    checked[d.name] = _now()
                    if not quiet:
                        print(f"  {title}: up to date")
                await queue_retries(d, entity)
                return

            # Write what was fetched even if the fetch died partway (and was
            # contiguous): the watermark moves with the rows. The files come
            # after, in the background; their rows say "pending" until then.
            fresh.sort(key=lambda r: r["id"], reverse=True)
            path = d / "messages.jsonl"
            existing = path.read_text(encoding="utf-8")
            if existing and not existing.endswith("\n"):
                existing += "\n"
            block = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in fresh)
            atomic_write(path, block + existing)
            tally["added"] += len(fresh)
            if failure:
                tally["failed"] += 0 if stopped else 1
            else:
                checked[d.name] = _now()
            note = (" (stopped: the rest comes with the next sync)" if stopped
                    else " (partial: the chat stopped with an error)" if failure else "")
            files = [(wanted[r["id"]], r) for r in fresh if r["id"] in wanted and r.get("media_pending")]
            more = f", {len(files)} file(s) to download" if files else ""
            print(f"  {title}: +{len(fresh)} new{note}{more}", flush=True)
            pool.add(d, files)
            await queue_retries(d, entity, {r["id"] for r in fresh})

            # Keep the title current; a renamed chat otherwise keeps a stale one.
            try:
                fresh_meta = entity_meta(entity)
                if store.meta(d).get("partial"):  # still missing older messages: keep saying so
                    fresh_meta["partial"] = True
                write_json(d / "channel.json", fresh_meta)
            except Exception as exc:  # noqa: BLE001
                applog.caught("sync: channel.json not refreshed", exc, ctx.base)
                print(f"  {title}: the chat's name could not be refreshed this time.", file=sys.stderr)

        async def queue_retries(d: Path, entity, skip=frozenset()) -> None:
            """Files an earlier copy or sync did not get (pending, failed, or skipped by a
            narrower Files choice) are tried again now. `skip`: rows of this run, queued already."""
            if pool is None or ctx.stop():
                return
            try:
                jobs = await retry_jobs(client, ctx, entity, d)
            except Exception as exc:  # noqa: BLE001
                applog.caught("sync: files to try again", exc, ctx.base)
                return
            pool.add(d, [j for j in jobs if j[1]["id"] not in skip])

        try:
            await asyncio.gather(*(one(d) for d in dirs))
            if pool is not None:
                if pool.total:
                    print(f"Files: downloading {pool.total} file(s) in the background ...", flush=True)
                await pool.finish()
        finally:
            if pool is not None:
                await pool.close()
        if pool is not None:
            if pool.total:
                rest = pool.total - pool.got
                tail = f"; {rest} not yet (the next sync tries again)" if rest else ""
                print(f"Files: {pool.got} downloaded{tail}.", flush=True)

    if not dry_run:
        store.merge_senders(names)
        if checked:
            state = store.sync_state()
            state.update(checked)
            write_json(store.home / "sync-state.json", state)
        write_summary(store)
    failed, added_total = tally["failed"], tally["added"]
    tail = f", {failed} chat(s) failed or skipped" if failed else ""
    if tally["stopped"]:
        print(f"Stopped: {added_total} new message(s) saved{tail}. "
              "Press Get new messages (or run tgkit sync) to get the rest.")
        return 1 if failed else 0
    print(f"Done: {added_total} new message(s){tail}.")
    return 1 if failed else 0


def _retry_wanted(row: dict) -> bool:
    if not row.get("media") or row.get("media_path"):
        return False
    if int(row.get("media_tries") or 0) >= MEDIA_TRIES:
        return False
    return bool(row.get("media_pending") or row.get("media_error") or row.get("media_skipped"))


async def retry_jobs(client, ctx: "Ctx", entity, d: Path) -> list:
    """(message, row) pairs for the files of this chat that are still to be fetched:
    recorded as pending or failed, or skipped by a Files choice that now allows them."""
    get = getattr(client, "get_messages", None)
    if get is None or ctx.media == "none":
        return []
    want: dict = {}
    path = d / "messages.jsonl"
    for line in path.read_text(encoding="utf-8").splitlines() if path.exists() else []:
        try:
            row = json.loads(line) if line.strip() else None
        except json.JSONDecodeError:
            continue
        if row and _retry_wanted(row):
            probe = dict(row)
            if want_file(ctx, probe):
                want[row["id"]] = probe
        if len(want) >= RETRY_FILES_MAX:
            break
    if not want:
        return []
    msgs = await get(entity, ids=list(want))
    jobs = []
    for msg in msgs or []:
        row = want.get(getattr(msg, "id", None)) if msg is not None else None
        if row is not None and getattr(msg, "media", None):
            jobs.append((msg, row))
    return jobs


def write_summary(store: Store) -> None:
    """Rebuild summary.json (an inventory of every chat) from what is on disk."""
    rec = []
    for d in store.chat_dirs():
        rs = store.rows(d)
        media_n = len(list((d / "media").iterdir())) if (d / "media").exists() else 0
        rec.append({
            # Must be a usable ADDRESS, never the folder name or the unsigned id.
            "channel": str(target_for(d) or d.name),
            "folder": d.name,
            "title": store.title(d),
            "messages": len(rs),
            "media_downloaded": media_n,
            "newest_id": rs[-1]["id"] if rs else None,
            "newest_date": rs[-1].get("date") if rs else None,
            "out": str(d),
        })
    write_json(store.home / "summary.json", rec)


# ------------------------------------------------------------------ add chats


def folder_for(entity, label: str) -> str:
    """The chat id is part of the name: two chats can share a title, and every
    title with no ASCII letters slugs to the same word."""
    meta = entity_meta(entity)
    return f"{slug(meta.get('username') or meta.get('title') or label)}-{meta['id']}"


def chat_key(meta: dict) -> tuple:
    return (meta.get("type"), meta.get("id"))


def followed_keys(store) -> set:
    """Chats already in Telegram Assistant, by id from channel.json, whatever their folder is called."""
    return {chat_key(store.meta(d)) for d in store.chat_dirs() if store.meta(d).get("id") is not None}


def _same_chat(store: Store, meta: dict, final: Path) -> Path | None:
    """The folder already holding this chat (by folder name or by id), or None."""
    if final.exists():
        return final
    key = chat_key(meta)
    return next((d for d in store.chat_dirs() if store.meta(d).get("id") is not None
                 and chat_key(store.meta(d)) == key), None)


def partial_keys(store: Store) -> set:
    """Chats whose first copy stopped early (channel.json "partial"): Retry copies them again."""
    return {chat_key(store.meta(d)) for d in store.chat_dirs() if store.meta(d).get("partial")}


async def add_chat(client, ctx: Ctx, entity, label: str, limit: int | None, on_count=None,
                   beat=None) -> dict:
    """Fetch a chat that has no folder yet. Refuses if its folder exists, unless
    that folder is a first copy that stopped early ("partial"): then the chat is
    copied again and the old folder is moved aside (`.partial-old-*`), never deleted.

    The original seeding path opened messages.jsonl in "w" mode and once
    destroyed a dump. Here the chat is built in a hidden staging folder and
    renamed into place only when complete; an existing folder is never touched.
    """
    store = ctx.store
    meta = entity_meta(entity)
    title = meta.get("title") or display_name(entity) or label
    base = folder_for(entity, label)
    final = store.chats / base
    prev = _same_chat(store, meta, final)
    if prev is not None and not store.meta(prev).get("partial"):
        raise UserError(
            f"'{title}' is already in Telegram Assistant.",
            "To get its new messages run: tgkit sync",
        )
    staging = store.chats / f".adding-{base}"
    if staging.exists():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        staging.rename(staging.with_name(f"{staging.name}.old-{stamp}"))
    staging.mkdir(parents=True)

    flood = _flood_error()
    rows: list[dict] = []
    names: dict = {}
    wanted: dict = {}
    print(f"Adding {title} ...", flush=True)

    def counted(n):
        if on_count is not None:
            on_count(n)
        if n % 200 == 0:
            print(f"  {n} messages so far", flush=True)
    # Text only here: the files of this chat download after it is saved (MediaPool).
    failure = await read_text(client, ctx, entity, rows, names, flood, beat, counted,
                              limit=limit, msgs=wanted)
    stopped = isinstance(failure, Stopped)
    if failure is not None and not stopped:
        applog.caught("add: copying a chat failed", failure, ctx.base)

    if not rows:  # plain words only: the error's kind and detail are in the log file
        with contextlib.suppress(OSError):
            staging.rmdir()  # empty: nothing was read
        if stopped:
            raise Stopped()
        raise UserError(
            f"Could not read any messages from '{title}'.",
            "Check that the chat still exists and that you are a member, then try again.",
        )
    # Newest first, like every messages.jsonl. A seed that stopped early holds
    # the newest N with no hole ABOVE them, so later syncs stay correct.
    rows.sort(key=lambda r: r["id"], reverse=True)
    write_json(staging / "channel.json", {**meta, "partial": True} if failure else meta)
    atomic_write(staging / "messages.jsonl",
                 "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    if final.exists() and final != prev:  # checked BEFORE moving anything aside
        raise UserError(f"'{title}' appeared in Telegram Assistant while adding it; nothing was overwritten.",
                        f"The fetched copy was left in {staging}.")
    if prev is not None and prev.exists():  # the earlier, cut-short copy: kept aside
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        prev.rename(store.chats / f".partial-old-{prev.name}-{stamp}")
    if final.exists():
        raise UserError(f"'{title}' appeared in Telegram Assistant while adding it; nothing was overwritten.",
                        f"The fetched copy was left in {staging}.")
    staging.rename(final)
    store.merge_senders(names)
    state = store.sync_state()
    state[base] = _now()
    write_json(store.home / "sync-state.json", state)
    note = " (stopped early; older messages were not fetched)" if failure else ""
    jobs = [(wanted[r["id"]], r) for r in rows if r["id"] in wanted and r.get("media_pending")]
    more = f", {len(jobs)} file(s) to download" if jobs else ""
    print(f"  {title}: {len(rows)} messages{more}{note}", flush=True)
    return {"title": title, "folder": base, "messages": len(rows), "files": len(jobs), "jobs": jobs,
            "partial": failure is not None, "stopped": stopped}


def _plain_dir(p: Path) -> bool:
    """A real folder: not a link and not a Windows junction (whose target is elsewhere)."""
    try:
        if p.is_symlink() or not p.is_dir():
            return False
        junction = getattr(p, "is_junction", None)
        if junction is not None and junction():
            return False
        return not getattr(os.lstat(p), "st_file_attributes", 0) & 0x400  # REPARSE_POINT
    except OSError:
        return False


def clean_staging(store: Store) -> list[str]:
    """Remove the staging folders (`chats/.adding-*`) a copy left when the window was
    closed mid-way. Run under the sync lock, so no copy is using them. A staging folder
    is never a chat: a chat is only ever renamed out of it when complete, so a folder
    without the `.adding-` prefix (every committed chat) is never touched."""
    gone = []
    if not store.chats.is_dir():
        return gone
    for p in store.chats.iterdir():
        if p.name.startswith(".adding-") and _plain_dir(p):
            try:
                shutil.rmtree(p)
                gone.append(p.name)
            except OSError as exc:
                applog.caught("add: an old staging folder was not removed", exc, store.home)
    return gone


async def list_dialogs(client, limit: int = 300) -> list[dict]:
    out = []
    async for dlg in client.iter_dialogs(limit=limit):
        if getattr(dlg, "is_group", False):
            kind = "group"
        elif getattr(dlg, "is_channel", False):
            kind = "channel"
        else:
            kind = "person"
        out.append({
            "name": dlg.name or "(no name)",
            "kind": kind,
            "id": dlg.id,
            "username": getattr(dlg.entity, "username", None),
            "entity": dlg.entity,
        })
    return out


def _dlg_key(dlg: dict) -> str:
    return str(dlg["id"] if dlg.get("id") is not None else getattr(dlg["entity"], "id", dlg["name"]))


async def add_chats(ctx: Ctx, query: str | None, limit: int | None, pick, progress=None) -> int:
    """`pick(dialogs, query)` returns the dialogs the user chose (each has an "added" flag).

    `progress(event)` hears every step, for the window's per-chat rows:
    {"type": "switched", ...}, {"type": "plan", "chats": [{id, name}]} and
    {"type": "chat", "id", "state": copying|done|error, "count", "message", "hint"}.
    One chat failing never stops the others; each failure is reported on its own.

    Locks (R14): the sync lock first, then the client's session lock. Picking runs
    outside both, so a person choosing chats in a terminal blocks no sync."""
    report = progress or (lambda ev: None)
    chosen = None
    with SyncLock(ctx.store.home, "add: listing chats"):
        async with connected(ctx) as client:
            # Logged in to another account than the chats shown: switch first (never mix).
            res = account.adopt(ctx.base, await session_me(client), locked=True)
            if res.get("switched"):
                report({"type": "switched", **res})
            ctx.cfg = config.load(ctx.base)
            if query and (query.startswith("@") or "t.me/" in query or re.fullmatch(r"-?\d+", query)):
                await client.get_dialogs()
                try:
                    entity = await client.get_entity(as_target(query))
                except Exception:  # noqa: BLE001
                    raise UserError(f"Telegram has no chat '{query}' that you can read.",
                                    "Check the spelling, or run tgkit add to pick from a list.") from None
                chosen = [{"name": display_name(entity) or query, "entity": entity}]
            else:
                dialogs = await list_dialogs(client)
                existing = {d.name for d in ctx.store.chat_dirs()}
                keys, cut = followed_keys(ctx.store), partial_keys(ctx.store)
                for dlg in dialogs:
                    # a first copy cut short is offered again (every picker: window and terminal)
                    dlg["partial"] = chat_key(entity_meta(dlg["entity"])) in cut
                    dlg["added"] = (folder_for(dlg["entity"], dlg["name"]) in existing
                                    or chat_key(entity_meta(dlg["entity"])) in keys) and not dlg["partial"]
    if chosen is None:
        chosen = pick(dialogs, query)
    if not chosen:
        return 0
    report({"type": "plan", "chats": [{"id": _dlg_key(d), "name": d["name"]} for d in chosen]})
    n = {"added": 0, "stored": 0, "cut_short": 0}  # stored: folders written; added: copied in full
    stopped_msg = ("Stopped before this chat was copied.", "Press Retry for this chat.")
    # The same lock as sync: add_chat checks its folders, then renames into
    # chats/ and rewrites sync-state.json, which a running sync also rewrites.
    with SyncLock(ctx.store.home, "add") as lock:
        clean_staging(ctx.store)  # left by a window closed mid-copy; no copy runs now
        async with connected(ctx) as client:
            pool = MediaPool(client, ctx, _flood_error(), lock.beat)
            sem = asyncio.Semaphore(CHATS_AT_ONCE)

            async def one(dlg) -> None:
                key = _dlg_key(dlg)
                async with sem:
                    if ctx.stop():
                        report({"type": "chat", "id": key, "state": "stopped", "message": stopped_msg[0],
                                "hint": stopped_msg[1]})
                        return
                    lock.beat(f"add: {dlg['name']}")
                    report({"type": "chat", "id": key, "state": "copying", "count": 0})

                    def counted(count, key=key):
                        lock.beat()
                        report({"type": "chat", "id": key, "state": "copying", "count": count})
                    try:
                        got = await add_chat(client, ctx, dlg["entity"], dlg["name"], limit, counted, lock.beat)
                    except Stopped:
                        report({"type": "chat", "id": key, "state": "stopped", "message": stopped_msg[0],
                                "hint": stopped_msg[1]})
                        return
                    except UserError as exc:
                        print(exc.render())
                        report({"type": "chat", "id": key, "state": "error", "message": exc.message,
                                "hint": exc.hint})
                        return
                    except Exception as exc:  # noqa: BLE001  (one chat failing never stops the others)
                        applog.caught("add: a chat failed", exc, ctx.base)
                        err = UserError(f"Could not copy '{dlg['name']}'.",
                                        "Check your internet connection, then press Retry for this chat.")
                        print(err.render())
                        report({"type": "chat", "id": key, "state": "error", "message": err.message,
                                "hint": err.hint})
                        return
                n["stored"] += 1
                n["added"] += 0 if got["partial"] else 1
                n["cut_short"] += 1 if got["partial"] else 0
                ev = {"type": "chat", "id": key, "state": "stopped" if got["stopped"] else "done",
                      "count": got["messages"], "partial": got["partial"],
                      "files_pending": got["files"], "files": 0}
                if got["stopped"]:  # saved and searchable, marked partial: Retry copies it again
                    ev.update(message=f"Stopped: {got['messages']} messages of '{dlg['name']}' are saved; "
                                      "older messages were not copied yet.",
                              hint="Press Retry for this chat to copy the rest.")
                elif got["partial"]:  # a copy cut short is not a success: say so, offer Retry
                    ev.update(message=f"Copied only {got['messages']} messages of '{dlg['name']}': "
                                      "the copy stopped early, older messages are missing.",
                              hint="Check your internet connection, then press Retry for this chat.")
                report(ev)

                def on_files(left, done, skipped=0, ev=ev):
                    report({**ev, "files_pending": left, "files": done, "files_skipped": skipped})
                pool.add(ctx.store.chats / got["folder"], got["jobs"], on_files)

            try:
                await asyncio.gather(*(one(d) for d in chosen))
                await pool.finish()  # the files of every chat, after all the text
            finally:
                await pool.close()
        if n["stored"]:
            write_summary(ctx.store)
    if n["added"]:
        print(f"Added {n['added']} chat(s). Try: tgkit summary")
    if pool.total > pool.got:
        print(f"{pool.total - pool.got} file(s) were not downloaded yet; tgkit sync tries them again.")
    if n["cut_short"]:
        print(f"{n['cut_short']} chat(s) stopped early and are missing older messages. "
              "Run tgkit add again and pick them to copy them again.")
    return 0


async def adopt_session(ctx: Ctx) -> dict:
    """Who the login file belongs to now. Another account than the chats shown
    switches to that account's chats (R15). The window runs this when it opens.
    Sync lock first, then the client (R14)."""
    with SyncLock(ctx.store.home, "account check"):
        async with connected(ctx) as client:
            me = await session_me(client)
            res = account.adopt(ctx.base, me, locked=True)
    return {**res, "me": me}


# -------------------------------------------------------- search, names, login


async def server_search(ctx: Ctx, query: str, dirs: list[Path], limit: int) -> int:
    """Ask Telegram itself: finds messages that are not in the local copy."""
    store = ctx.store
    total = 0
    async with connected(ctx) as client:
        # Read-only, but never through another account's login: the chats shown belong
        # to one account (the same check sync and the window make). It switches nothing.
        owner, me = account.owner(ctx.base), await session_me(client)
        if other_account(owner, me):
            now = me.get("name") or (f"@{me['username']}" if me.get("username") else "another Telegram account")
            shown = owner.get("name") or (f"@{owner['username']}" if owner.get("username") else "another account")
            raise UserError(
                f"Nothing was searched: this computer is now logged in to {now}, "
                f"but the chats in Telegram Assistant belong to {shown}.",
                f"To use {now}'s chats, press Get new messages in the Telegram Assistant window "
                "(or run: tgkit sync); Telegram Assistant then switches to them and keeps the others aside. "
                "Then search again.",
            )
        await client.get_dialogs()
        for d in dirs:
            target = target_for(d)
            if target is None:
                continue
            try:
                entity = await client.get_entity(target)
            except Exception as exc:  # noqa: BLE001
                applog.caught("search --online: a chat could not be opened", exc, ctx.base)
                print(f"Skipped {store.title(d)}: Telegram could not open this chat.", file=sys.stderr)
                continue
            lines = []
            async for msg in client.iter_messages(entity, search=query, limit=limit):
                names: dict = {}
                remember_sender(names, msg)
                store.senders().update(names)
                lines.append(store.fmt(message_row(msg, media_summary(msg))))
            if lines:
                print(f"=== {store.title(d)} (on Telegram) ===")
                print("\n".join(lines))
                print()
                total += len(lines)
    print(f"{total} message(s) found on Telegram." if total else
          f"Telegram found nothing for '{query}' in your chats.")
    return 0


async def resolve_names(ctx: Ctx, dirs: list[Path]) -> int:
    store = ctx.store
    ids = {r["sender_id"] for d in dirs for r in store.rows(d) if r.get("sender_id")}
    known = store.senders()
    todo = [i for i in ids if str(i) not in known]
    print(f"{len(ids)} people seen, {len(todo)} without a name yet")
    names: dict = {}
    if todo:
        async with connected(ctx) as client:
            await client.get_dialogs()
            for sid in todo:
                try:
                    ent = await client.get_entity(sid)
                except Exception:  # noqa: BLE001
                    continue
                name = display_name(ent)
                if name:
                    names[str(sid)] = name
        store.merge_senders(names)
    for sid, name in sorted(store.senders().items(), key=lambda kv: kv[1]):
        print(f"  {name}")
    return 0


async def login(ctx: Ctx, ask_phone, ask_code, ask_password) -> dict:
    """Interactive first login. Only ever run by the user, in their own window."""
    with SessionLock(ctx.base):
        client = make_client(ctx.base, ctx.cfg)
        await client.connect()
        try:
            if not await client.is_user_authorized():
                await client.start(phone=ask_phone, code_callback=ask_code, password=ask_password)
            me = await client.get_me()
            return {"id": me.id, "name": display_name(me), "username": getattr(me, "username", None)}
        finally:
            await _maybe_await(client.disconnect())


async def logout(ctx: Ctx) -> bool:
    """End this computer's Telegram login on Telegram's side (like Settings > Devices >
    Terminate). True when Telegram confirmed it. The caller removes the local file."""
    with SessionLock(ctx.base):
        client = make_client(ctx.base, ctx.cfg)
        await client.connect()
        try:
            if not await client.is_user_authorized():
                return False
            return bool(await client.log_out())
        finally:
            await _maybe_await(client.disconnect())
