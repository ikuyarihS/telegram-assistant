"""Everything that talks to Telegram: sync, add a chat, online search, names, login.

Ported from the original single-user tool with its sync semantics UNCHANGED.
Read docs/MAINTENANCE.md before changing anything here. The short version:

  * sync only fetches messages newer than the newest id already on disk; it
    never rewrites history and never re-downloads media;
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
import re
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


class Ctx:
    """What a network command needs: the data folder, its store and config."""

    def __init__(self, base: Path, store: Store, cfg: dict):
        self.base = Path(base)
        self.store = store
        self.cfg = cfg
        self.max_media = config.max_media_bytes(self.base, cfg)


def make_client(base: Path, cfg: dict):
    api_id, api_hash = config.credentials(base, cfg)
    try:
        from telethon import TelegramClient  # noqa: PLC0415
    except ImportError:
        raise UserError(
            "The Telegram part of TG Kit is missing on this computer.",
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


async def _download(client, msg, media, row, media_dir: Path, rel_folder: str, cap: int, flood) -> int:
    """Download one attachment; failures are recorded on the row, never fatal."""
    size = media.get("size") or 0
    if size and size > cap:
        row["media_skipped"] = f"size {size} > cap {cap}"
        return 0
    media_dir.mkdir(parents=True, exist_ok=True)
    try:
        path = await client.download_media(msg, file=str(media_dir))
        if path:
            row["media_path"] = str(Path(rel_folder) / "media" / Path(path).name)
            return 1
    except flood as exc:
        print(f"  Telegram asked to wait {exc.seconds}s; waiting", flush=True)
        await asyncio.sleep(exc.seconds + 1)
    except Exception as exc:  # noqa: BLE001
        row["media_error"] = f"{type(exc).__name__}: {exc}"
    return 0


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
    with SyncLock(ctx.store.home):
        return await _sync_chats(ctx, dirs, quiet, dry_run)


async def _sync_chats(ctx: Ctx, dirs: list[Path], quiet: bool, dry_run: bool) -> int:
    store = ctx.store
    # Every watermark is read BEFORE connecting: a damaged file aborts with a
    # clear message, not after a login round-trip.
    baseline = {d: newest_id(d) for d in dirs}
    flood = _flood_error()

    added_total = 0
    failed = 0
    names: dict = {}
    checked: dict = {}
    async with connected(ctx) as client:
        # The chats shown belong to one account: never copy another account's
        # messages into them (a login made elsewhere, the old "me" kept).
        owner, me = account.owner(ctx.base), await session_me(client)
        if not dry_run and owner and me and str(owner.get("id")) != str(me["id"]):
            raise UserError(
                "This computer is now logged in to another Telegram account.",
                "Open Settings and press Add chats once: TG Kit then shows that account's chats.",
            )
        # Private chats resolve by numeric id ONLY after the dialog cache is
        # filled; without it a cold session skips nearly everything and still
        # prints "0 new", a false clean.
        await client.get_dialogs()
        for d in dirs:
            title = store.title(d)
            target = target_for(d)
            if target is None:
                print(f"Skipped {title}: no usable address in channel.json", file=sys.stderr)
                continue

            after = baseline[d]
            if after == 0:
                # A folder with no rows is "never fetched", NOT "fetch from the
                # beginning": min_id=0 would pull a chat's whole history.
                print(f"Skipped {title}: it has no messages on disk yet. sync only fetches "
                      f"what is new; add a chat with: tgkit add", file=sys.stderr)
                continue

            try:
                entity = await client.get_entity(target)
            except Exception as exc:  # noqa: BLE001
                applog.caught("sync: a chat could not be opened", exc, ctx.base)
                print(f"Skipped {title}: {type(exc).__name__}: {exc}", file=sys.stderr)
                failed += 1
                continue

            fresh: list[dict] = []
            failure: BaseException | None = None
            try:
                async for msg in client.iter_messages(entity, min_id=after):
                    media = media_summary(msg)
                    row = message_row(msg, media)
                    remember_sender(names, msg)
                    if media and not dry_run:
                        await _download(client, msg, media, row, d / "media", d.name, ctx.max_media, flood)
                    fresh.append(row)
            except flood as exc:
                print(f"  {title}: Telegram asked to wait {exc.seconds}s; stopping this chat",
                      file=sys.stderr)
                failure = exc
            except Exception as exc:  # noqa: BLE001
                applog.caught("sync: fetching new messages failed", exc, ctx.base)
                print(f"  {title}: {type(exc).__name__}: {exc}", file=sys.stderr)
                failure = exc

            if dry_run:
                print(f"  {title}: would fetch {len(fresh)} message(s) after #{after}")
                continue

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

            if not fresh:
                if failure is not None:
                    failed += 1
                else:
                    checked[d.name] = _now()
                    if not quiet:
                        print(f"  {title}: up to date")
                continue

            # Write what was fetched even if the fetch died partway (and was
            # contiguous): the media is already on disk, and the watermark
            # moves with the rows.
            fresh.sort(key=lambda r: r["id"], reverse=True)
            path = d / "messages.jsonl"
            existing = path.read_text(encoding="utf-8")
            if existing and not existing.endswith("\n"):
                existing += "\n"
            block = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in fresh)
            atomic_write(path, block + existing)
            added_total += len(fresh)
            if failure:
                failed += 1
            else:
                checked[d.name] = _now()
            note = " (partial: the chat stopped with an error)" if failure else ""
            print(f"  {title}: +{len(fresh)} new{note}")

            # Keep the title current; a renamed chat otherwise keeps a stale one.
            try:
                write_json(d / "channel.json", entity_meta(entity))
            except Exception as exc:  # noqa: BLE001
                print(f"  {title}: channel.json not refreshed ({type(exc).__name__})", file=sys.stderr)

    if not dry_run:
        store.merge_senders(names)
        if checked:
            state = store.sync_state()
            state.update(checked)
            write_json(store.home / "sync-state.json", state)
        write_summary(store)
    tail = f", {failed} chat(s) failed or skipped" if failed else ""
    print(f"Done: {added_total} new message(s){tail}.")
    return 1 if failed else 0


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
    """Chats already in TG Kit, by id from channel.json, whatever their folder is called."""
    return {chat_key(store.meta(d)) for d in store.chat_dirs() if store.meta(d).get("id") is not None}


async def add_chat(client, ctx: Ctx, entity, label: str, limit: int | None) -> dict:
    """Fetch a chat that has no folder yet. Refuses if its folder exists at all.

    The original seeding path opened messages.jsonl in "w" mode and once
    destroyed a dump. Here the chat is built in a hidden staging folder and
    renamed into place only when complete; an existing folder is never touched.
    """
    store = ctx.store
    meta = entity_meta(entity)
    title = meta.get("title") or display_name(entity) or label
    base = folder_for(entity, label)
    final = store.chats / base
    if final.exists() or chat_key(meta) in followed_keys(store):
        raise UserError(
            f"'{title}' is already in TG Kit.",
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
    files = 0
    failure: BaseException | None = None
    print(f"Adding {title} ...", flush=True)
    try:
        async for msg in client.iter_messages(entity, limit=limit):
            media = media_summary(msg)
            row = message_row(msg, media)
            remember_sender(names, msg)
            if media:
                files += await _download(client, msg, media, row, staging / "media", base,
                                         ctx.max_media, flood)
            rows.append(row)
            if len(rows) % 200 == 0:
                print(f"  {len(rows)} messages so far", flush=True)
    except Exception as exc:  # noqa: BLE001
        applog.caught("add: copying a chat failed", exc, ctx.base)
        failure = exc

    if not rows:
        why = f" ({type(failure).__name__})" if failure else ""
        raise UserError(
            f"Could not read any messages from '{title}'{why}.",
            "Check that the chat still exists and that you are a member, then try again.",
        )
    # Newest first, like every messages.jsonl. A seed that stopped early holds
    # the newest N with no hole ABOVE them, so later syncs stay correct.
    rows.sort(key=lambda r: r["id"], reverse=True)
    write_json(staging / "channel.json", meta)
    atomic_write(staging / "messages.jsonl",
                 "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    if final.exists():
        raise UserError(f"'{title}' appeared in TG Kit while adding it; nothing was overwritten.",
                        f"The fetched copy was left in {staging}.")
    staging.rename(final)
    store.merge_senders(names)
    state = store.sync_state()
    state[base] = _now()
    write_json(store.home / "sync-state.json", state)
    note = " (stopped early; older messages were not fetched)" if failure else ""
    print(f"  {title}: {len(rows)} messages, {files} file(s){note}")
    return {"title": title, "folder": base, "messages": len(rows), "files": files,
            "partial": failure is not None}


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


async def add_chats(ctx: Ctx, query: str | None, limit: int | None, pick) -> int:
    """`pick(dialogs, query)` returns the dialogs the user chose (each has an "added" flag)."""
    async with connected(ctx) as client:
        # Logged in to another account than the chats shown: switch first (never mix).
        account.adopt(ctx.base, await session_me(client))
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
            keys = followed_keys(ctx.store)
            for dlg in dialogs:
                dlg["added"] = (folder_for(dlg["entity"], dlg["name"]) in existing
                                or chat_key(entity_meta(dlg["entity"])) in keys)
            chosen = pick(dialogs, query)
        added = 0
        # The same lock as sync: add_chat checks its folders, then renames into
        # chats/ and rewrites sync-state.json, which a running sync also rewrites.
        with SyncLock(ctx.store.home):
            for dlg in chosen:
                try:
                    await add_chat(client, ctx, dlg["entity"], dlg["name"], limit)
                    added += 1
                except UserError as exc:
                    print(exc.render())
    if added:
        write_summary(ctx.store)
        print(f"Added {added} chat(s). Try: tgkit summary")
    return 0


# -------------------------------------------------------- search, names, login


async def server_search(ctx: Ctx, query: str, dirs: list[Path], limit: int) -> int:
    """Ask Telegram itself: finds messages that are not in the local copy."""
    store = ctx.store
    total = 0
    async with connected(ctx) as client:
        await client.get_dialogs()
        for d in dirs:
            target = target_for(d)
            if target is None:
                continue
            try:
                entity = await client.get_entity(target)
            except Exception as exc:  # noqa: BLE001
                print(f"Skipped {store.title(d)}: {type(exc).__name__}", file=sys.stderr)
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
