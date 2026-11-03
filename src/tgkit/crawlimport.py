"""Bring in a copy made with tg-crawl: `tgkit import-crawl <folder>`, and Settings,
Coming from tg-crawl, in the window (which runs that command as its own child).

tg-crawl keeps out/<chat>/channel.json, messages.jsonl (newest first, the same row
shape as Telegram Assistant) and media/, plus out/senders.json. This module COPIES
them into chats/<Title>-<id>/. The tg-crawl folder is only ever read (shutil.copy2
reads the source), so it stays byte for byte as it was, and nothing is downloaded.

Rules (docs/MAINTENANCE.md R18):
- Under the sync lock, like sync and add; the Telegram check takes the session lock
  inside it (SyncLock first, then SessionLock).
- One chat at a time, built in a hidden `.adding-<folder>` staging folder. A new
  chat is renamed into place when complete; an existing chat gets its new files
  first, then its messages.jsonl in one atomic write. A chat that fails keeps the
  copy it had (at most a few extra files in media/, R5).
- Merged by message id: no duplicates. tg-crawl records no edits, so on the same
  id the Telegram Assistant row is kept; only a file it lacks is filled in.
- The sync watermark is the highest id on disk (net.newest_id), so the next sync
  fetches only newer messages. A tg-crawl copy that starts after the Telegram
  Assistant copy ends would leave a hole no sync fills (R4): that chat is not merged.
- A tg-crawl file with a line that cannot be read is not imported (R2).
- After each chat: every tg-crawl id must be in the copy, else a warning row.
"""

import filecmp
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import applog, config, net
from .errors import UserError
from .store import Store, SyncLock, atomic_write, read_json, write_json

# Row keys that say a file is still to come; a file copied from tg-crawl clears them.
MEDIA_TODO = ("media_pending", "media_error", "media_tries", "media_skipped")
NOTES = {
    "seen": "",
    "not_seen": "Your Telegram account does not have this chat: it is kept and searchable, "
                "but Get new messages skips it.",
    "not_checked": "",
}


# Why an item of out/ is not a chat to import (the window shows each code in its language).
SKIP = {"loose": "a loose file, not a chat", "hidden": "a hidden folder",
        "partial": "an unfinished copy (a .partial folder)", "link": "a link to another folder",
        "nomessages": "no messages.jsonl inside", "noid": "no channel.json with the chat's id"}


class Stopped(Exception):
    """Stop was pressed: the chat being copied is left as it was."""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# ------------------------------------------------------------------ reading tg-crawl


def _is_chat(p: Path) -> bool:
    return p.is_dir() and (p / "messages.jsonl").is_file()


def source_folder(folder) -> Path:
    """The tg-crawl out/ folder: the one given, or its out/ when it holds no chat itself."""
    raw = str(folder or "").strip().strip('"')
    if not raw:
        raise UserError("Pick the tg-crawl folder first.", r"For example: D:\tg-crawl")
    p = Path(raw).expanduser()
    if not p.is_absolute():
        raise UserError("Type the full path of the tg-crawl folder.", r"For example: D:\tg-crawl")
    if not p.is_dir():
        raise UserError("That folder does not exist.", "Check the path, or pick the folder again.")
    try:
        has_chat = any(_is_chat(c) for c in p.iterdir())
    except OSError:
        raise UserError("That folder cannot be read.", "Pick another folder.") from None
    if not has_chat and (p / "out").is_dir():
        p = p / "out"
    return p


def _not_own(out: Path, home: Path) -> None:
    """Never import Telegram Assistant's own data folder into itself."""
    try:
        o, h = out.resolve(), Path(home).resolve()
    except OSError:
        return
    if o == h or o.is_relative_to(h) or h.is_relative_to(o):
        raise UserError("That is Telegram Assistant's own data folder, not a tg-crawl copy.",
                        "Pick the tg-crawl folder (the one holding out).")


def read_rows(path: Path) -> tuple[list[dict], int]:
    """(rows, lines that could not be read). A row needs a whole-number id."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return [], 1
    rows, bad = [], 0
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except ValueError:
            bad += 1
            continue
        if not isinstance(r, dict) or not isinstance(r.get("id"), int) or isinstance(r.get("id"), bool):
            bad += 1
            continue
        rows.append(r)
    return rows, bad


def media_source(out_root: Path, d: Path, rel) -> Path | None:
    """The tg-crawl file a row points at (media_path is relative to out/), or None.
    Only a file inside the tg-crawl folder is ever used."""
    if not rel or not isinstance(rel, str):
        return None
    parts = [x for x in rel.replace("\\", "/").split("/") if x not in ("", ".", "..")]
    if not parts:
        return None
    for cand in (out_root.joinpath(*parts), d / "media" / parts[-1]):
        try:
            real = cand.resolve()
            if real.is_file() and real.is_relative_to(out_root):
                return real
        except OSError:
            continue
    return None


def _title(meta: dict, name: str) -> str:
    return (meta.get("title") or " ".join(x for x in [meta.get("first_name"), meta.get("last_name")] if x)
            or name.replace("_", " "))


def _chat_info(out_root: Path, p: Path) -> dict:
    meta: dict = {}
    try:
        if (p / "channel.json").is_file():
            meta = json.loads((p / "channel.json").read_text(encoding="utf-8")) or {}
    except (OSError, ValueError, UnicodeDecodeError):
        meta = {}
    meta = meta if isinstance(meta, dict) else {}
    ident = meta.get("id")
    if not isinstance(ident, int) or isinstance(ident, bool):
        ident = int(p.name) if p.name.isdigit() else None
    if ident is None:
        return {"name": p.name, "why": SKIP["noid"], "code": "noid"}
    rows, bad = read_rows(p / "messages.jsonl")
    ids = {r["id"] for r in rows}
    files = sum(1 for r in rows if media_source(out_root, p, r.get("media_path")))
    return {"name": p.name, "dir": str(p), "id": ident, "title": _title(meta, p.name), "meta": meta,
            "messages": len(ids), "bad": bad, "first_id": min(ids) if ids else None,
            "last_id": max(ids) if ids else None, "files": files}


def senders(out: Path) -> dict:
    """tg-crawl's names (out/senders.json, or next to out/): sender id -> name."""
    for path in (out / "senders.json", out.parent / "senders.json"):
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, UnicodeDecodeError):
                return {}
            if isinstance(data, dict):
                return {str(k): v for k, v in data.items() if isinstance(v, str) and v.strip()}
    return {}


def scan(out: Path) -> dict:
    """What is in a tg-crawl out/ folder. Reads only."""
    out_root = out.resolve()
    chats, skipped = [], []
    for p in sorted(out.iterdir(), key=lambda x: x.name.casefold()):
        name = p.name
        if p.is_file():
            if name != "senders.json":
                skipped.append({"name": name, "why": SKIP["loose"], "code": "loose"})
            continue
        if not p.is_dir():
            continue
        code = None
        if name.startswith("."):
            code = "hidden"
        elif ".partial" in name:
            code = "partial"
        elif not net._plain_dir(p):
            code = "link"
        elif not (p / "messages.jsonl").is_file():
            code = "nomessages"
        if code:
            skipped.append({"name": name, "why": SKIP[code], "code": code})
            continue
        info = _chat_info(out_root, p)
        if info.get("why"):
            skipped.append(info)
        else:
            chats.append(info)
    names = senders(out)
    return {"folder": str(out), "chats": chats, "skipped": skipped, "names": len(names),
            "messages": sum(c["messages"] for c in chats if not c["bad"]),
            "files": sum(c["files"] for c in chats if not c["bad"])}


def preview(home: Path, folder) -> dict:
    """What an import would bring in: chats, messages, files, skipped items. Reads only."""
    out = source_folder(folder)
    _not_own(out, home)
    plan = scan(out)
    if not plan["chats"]:
        raise UserError("No tg-crawl chats were found in that folder.",
                        "Pick the tg-crawl folder (the one holding out), or its out folder.")
    store = Store(home)
    have = {store.meta(d).get("id") for d in store.chat_dirs()}
    for c in plan["chats"]:
        c["in_tgkit"] = c["id"] in have
        c.pop("meta", None)
    return plan


# ------------------------------------------------------------------ the Telegram check


def check_account(ctx) -> tuple[dict | None, str]:
    """({id: chat meta} of the chats the logged-in account has, ""), or (None, why not checked)."""
    from . import account  # noqa: PLC0415

    if not config.logged_in(ctx.base) or not config.has_credentials(ctx.base, ctx.cfg):
        return None, ("Not logged in to Telegram on this computer, so the chats were not checked "
                      "against your account.")

    async def go():
        async with net.connected(ctx) as client:  # the session lock, inside the sync lock
            if net.other_account(account.owner(ctx.base), await net.session_me(client)):
                return None
            found = {}
            for dlg in await net.list_dialogs(client, limit=None):
                meta = net.entity_meta(dlg["entity"])
                found[meta["id"]] = meta
            return found

    try:
        seen = net.run(go())
    except Exception as exc:  # noqa: BLE001  (offline or refused: the import goes on, unchecked)
        applog.caught("import-crawl: account check", exc, ctx.base)
        return None, "Telegram could not be asked which chats your account has, so the chats were not checked."
    if seen is None:
        return None, ("This computer is logged in to another Telegram account than the chats shown, so the "
                      "chats were not checked. Press Get new messages first, then import again.")
    return seen, ""


# ------------------------------------------------------------------ one chat


def _strict_rows(d: Path) -> dict:
    """Telegram Assistant's rows of a chat by id. A damaged line refuses (as sync does)."""
    net.newest_id(d)  # raises a plain UserError on a damaged line
    rows, _ = read_rows(d / "messages.jsonl")
    out: dict = {}
    for r in rows:
        out.setdefault(r["id"], r)
    return out


def _existing(store: Store, meta: dict) -> Path | None:
    for d in store.chat_dirs():
        m = store.meta(d)
        if m.get("id") == meta["id"] and (not m.get("type") or not meta.get("type")
                                          or m.get("type") == meta.get("type")):
            return d
    return None


def _same_file(a: Path, b: Path) -> bool:
    try:
        return a.stat().st_size == b.stat().st_size and filecmp.cmp(a, b, shallow=False)
    except OSError:
        return False


def _copy_in(src: Path, staged: Path, final_media: Path, beat) -> tuple[str, bool]:
    """Copy one tg-crawl file into the staging media folder. (name in media/, copied?).
    A file already in the chat with the same bytes is used as it is; another file
    with that name gets a new name ("name (2).pdf"). The source is only read."""
    stem, suffix = src.stem, src.suffix
    n = 1
    while True:
        name = src.name if n == 1 else f"{stem} ({n}){suffix}"
        there, inside = final_media / name, staged / name
        if there.exists() and not inside.exists():
            if _same_file(src, there):
                return name, False
        elif not inside.exists():
            break
        n += 1
    staged.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, staged / name)
    beat()
    return name, True


def _ids(path: Path) -> set:
    rows, _ = read_rows(path)
    return {r["id"] for r in rows}


def _row(src: dict) -> dict:
    return {"chat": src.get("title") or src["name"], "source_dir": src["name"], "folder": None,
            "source": src.get("messages", 0), "messages": None, "added": 0,
            "first_id": src.get("first_id"), "last_id": src.get("last_id"), "files": 0, "files_missing": 0,
            "account": None, "status": "ok", "note": "", "notes": []}


def _say(row: dict, status: str, *notes: str) -> dict:
    """Set a row's result; `notes` are whole sentences (the window shows each in its language)."""
    row["status"] = status
    row["notes"] = [n for n in notes if n]
    row["note"] = " ".join(row["notes"])
    return row


def import_chat(store: Store, out_root: Path, src: dict, seen: dict | None, beat=None, stop=None) -> dict:
    """Copy one tg-crawl chat in (see the module rules). Returns its report row."""
    beat = beat or (lambda *a: None)
    stop = stop or (lambda: False)
    row = _row(src)
    d = Path(src["dir"])
    rows, bad = read_rows(d / "messages.jsonl")
    if bad:
        return _say(row, "warning", f"{bad} line(s) of its messages.jsonl could not be read, so it was not "
                    "imported: a copy built from part of a file could miss messages for good.",
                    "Fix that file in tg-crawl, then import again.")
    if not rows:
        return _say(row, "skipped", "It has no messages.")
    by_src: dict = {}
    for r in rows:  # newest first: the first row of an id is kept
        by_src.setdefault(r["id"], r)
    meta = {k: v for k, v in src["meta"].items() if k not in ("partial", "from_crawl")}
    meta["id"] = src["id"]
    if seen is None:
        row["account"] = "not_checked"
    elif src["id"] in seen:
        meta.update({k: v for k, v in seen[src["id"]].items() if v is not None})
        row["account"] = "seen"
    else:
        row["account"] = "not_seen"
    title = _title(meta, src["name"])
    row["chat"] = title
    existing = _existing(store, meta)
    folder = existing.name if existing else f"{net.slug(meta.get('username') or meta.get('title') or src['name'])}-{src['id']}"
    row["folder"] = folder
    old = _strict_rows(existing) if existing else {}
    if old and min(by_src) > max(old) + 1:
        return _say(row, "warning", f"Not imported: the tg-crawl copy starts at message #{min(by_src)}, after "
                    f"Telegram Assistant's copy ends (#{max(old)}).",
                    "Messages in between would never be fetched, so Telegram Assistant's copy was left as it was.")

    final = store.chats / folder
    staging = store.chats / f".adding-{folder}"
    if staging.exists():
        staging.rename(staging.with_name(f"{staging.name}.old-{datetime.now().strftime('%Y%m%d-%H%M%S')}"))
    staging.mkdir(parents=True)
    staged_media = staging / "media"
    files = missing = 0
    try:
        merged = dict(old)
        taken: dict = {}
        for ident, r in by_src.items():
            if stop():
                raise Stopped()
            mine = old.get(ident)
            if mine is not None and (mine.get("media_path") or not r.get("media_path")):
                continue  # already there; tg-crawl records no edits
            target = dict(mine if mine is not None else r)
            rel = r.get("media_path")
            if rel:
                path = media_source(out_root, d, rel)
                if path is None:
                    missing += 1
                    if mine is not None:
                        continue
                    target["media_path"] = None
                    target["media_error"] = "the file was not in the tg-crawl copy"
                else:
                    if path not in taken:
                        taken[path] = _copy_in(path, staged_media, final / "media", beat)[0]
                    files += 1
                    target["media_path"] = str(Path(folder) / "media" / taken[path])
                    for key in MEDIA_TODO:
                        target.pop(key, None)
            merged[ident] = target
        text = "".join(json.dumps(merged[i], ensure_ascii=False) + "\n" for i in sorted(merged, reverse=True))
        src_mtime = (d / "messages.jsonl").stat().st_mtime
        if existing is None:
            atomic_write(staging / "messages.jsonl", text)
            # the copy is as old as tg-crawl's file, never shown as freshly updated
            os.utime(staging / "messages.jsonl", (src_mtime, src_mtime))
            write_json(staging / "channel.json", {**meta, "from_crawl": {"account": row["account"], "at": _now()}})
            if final.exists():
                raise UserError(f"'{title}' appeared in Telegram Assistant while importing it; nothing was overwritten.",
                                "Import again.")
            staging.rename(final)
        else:
            if staged_media.is_dir():
                (final / "media").mkdir(exist_ok=True)
                for f in staged_media.iterdir():
                    os.replace(f, final / "media" / f.name)
            old_mtime = (final / "messages.jsonl").stat().st_mtime
            atomic_write(final / "messages.jsonl", text)
            when = max(old_mtime, src_mtime)
            os.utime(final / "messages.jsonl", (when, when))
    finally:
        if staging.exists() and staging.name.startswith(".adding-") and net._plain_dir(staging):
            shutil.rmtree(staging, ignore_errors=True)  # only the staging folder made above

    # Check: every tg-crawl message must be in the copy now.
    now_ids = _ids(final / "messages.jsonl")
    lost = set(by_src) - now_ids
    row.update(messages=len(now_ids), added=len(now_ids) - len(old), files=files, files_missing=missing,
               source=len(by_src), first_id=min(by_src), last_id=max(by_src))
    notes = []
    if lost:
        notes.append(f"{len(lost)} of {len(by_src)} messages from tg-crawl are not in Telegram Assistant's copy.")
    if missing:
        notes.append(f"{missing} file(s) were not in the tg-crawl copy; Get new messages tries to download them.")
    notes.append(NOTES.get(row["account"]) or "")
    return _say(row, "warning" if lost else "ok", *notes)


# ------------------------------------------------------------------ the whole import


def _merge_names(store: Store, out: Path) -> int:
    names = senders(out)
    if not names:
        return 0
    try:
        known = read_json(store.home / "senders.json", {})
    except UserError:
        print("WARNING: senders.json is damaged; the names from tg-crawl were not added.", file=sys.stderr)
        return 0
    new = {k: v for k, v in names.items() if k not in known}  # names already known are kept
    store.merge_senders(new)
    return len(new)


def run_import(home: Path, folder, check: bool = True, stop=None, report=None) -> dict:
    """Import every chat of a tg-crawl copy. `report(event)` hears each step (the window)."""
    stop = stop or (lambda: False)
    report = report or (lambda ev: None)
    home = Path(home)
    store = Store(home)
    out = source_folder(folder)
    _not_own(out, home)
    plan = scan(out)
    if not plan["chats"]:
        raise UserError("No tg-crawl chats were found in that folder.",
                        "Pick the tg-crawl folder (the one holding out), or its out folder.")
    report({"type": "plan", "folder": plan["folder"], "chats": len(plan["chats"]), "messages": plan["messages"],
            "files": plan["files"], "skipped": plan["skipped"]})
    out_root = out.resolve()
    rows: list[dict] = []
    stopped = False
    with SyncLock(home, "import") as lock:
        net.clean_staging(store)  # left by a copy that was ended; no copy runs now
        cfg = config.load(home) if config.config_path(home).exists() else {}
        ctx = net.Ctx(home, store, cfg)
        seen, account_note = check_account(ctx) if check else (
            None, "The chats were not checked against your Telegram account (--offline).")
        for src in plan["chats"]:
            if stop():
                stopped = True
                row = _say(_row(src), "stopped", "Stopped before this chat was copied.")
            else:
                lock.beat(f"import: {src['title']}")
                report({"type": "chat", "chat": src["title"], "state": "copying"})
                try:
                    row = import_chat(store, out_root, src, seen, lambda *a: lock.beat(), stop)
                except Stopped:
                    stopped = True
                    row = _say(_row(src), "stopped", "Stopped while copying this chat; Telegram Assistant's "
                                                     "copy of it was left as it was.")
                except UserError as exc:
                    row = _say(_row(src), "failed", exc.message)
                except Exception as exc:  # noqa: BLE001  (one chat failing never stops the others)
                    applog.caught("import-crawl: a chat failed", exc, home)
                    row = _say(_row(src), "failed", "Could not import this chat; Telegram Assistant's copy was "
                                                    "left as it was (details in the log file).")
            rows.append(row)
            report({"type": "chat", **row, "state": row["status"]})
        names = _merge_names(store, out)
        if any(r["status"] in ("ok", "warning") and r.get("messages") for r in rows):
            net.write_summary(store)
    count = {s: sum(1 for r in rows if r["status"] == s) for s in ("ok", "warning", "failed", "skipped", "stopped")}
    return {"folder": plan["folder"], "rows": rows, "skipped": plan["skipped"], "names_added": names,
            "account_note": account_note, "counts": count, "stopped": stopped,
            "messages": sum(r["added"] for r in rows), "files": sum(r["files"] for r in rows)}


# ------------------------------------------------------------------ plain text


def render_preview(plan: dict) -> str:
    lines = [f"tg-crawl copy: {plan['folder']}",
             f"Found {len(plan['chats'])} chat(s), {plan['messages']} message(s), {plan['files']} file(s)."]
    for c in plan["chats"]:
        if c.get("bad"):
            lines.append(f"  {c['title']}: {c['bad']} line(s) cannot be read; this chat will be skipped.")
    for s in plan["skipped"]:
        lines.append(f"Skipped {s['name']}: {s['why']}.")
    return "\n".join(lines)


def _cut(text: str, n: int) -> str:
    text = str(text or "")
    return text if len(text) <= n else text[: n - 3] + "..."


RESULT_WORDS = {"ok": "ok", "warning": "WARNING", "failed": "FAILED", "skipped": "skipped", "stopped": "stopped"}


def render_report(res: dict) -> str:
    head = f"{'Chat':<34} {'tg-crawl':>9} {'now':>9} {'ids':>21} {'files':>6}  result"
    lines = ["", "Check after import (every tg-crawl message must be in Telegram Assistant):", head, "-" * len(head)]
    for r in res["rows"]:
        ids = f"#{r['first_id']}-#{r['last_id']}" if r.get("first_id") is not None else "-"
        now = r["messages"] if r.get("messages") is not None else "-"
        lines.append(f"{_cut(r['chat'], 34):<34} {r['source']:>9} {now:>9} {ids:>21} {r['files']:>6}  "
                     f"{RESULT_WORDS.get(r['status'], r['status'])}")
        if r.get("note"):
            lines.append(f"    {r['note']}")
    c = res["counts"]
    lines.append("")
    if res.get("account_note"):
        lines.append(res["account_note"])
    done = f"Done: {c['ok'] + c['warning']} chat(s) imported, {res['messages']} new message(s), {res['files']} file(s)"
    if res["names_added"]:
        done += f", names of {res['names_added']} people"
    lines.append(done + ".")
    bad = c["warning"] + c["failed"]
    if bad:
        lines.append(f"{bad} chat(s) need a look: see the WARNING and FAILED rows above.")
    if res["stopped"]:
        lines.append("Stopped: the chats not copied yet were left as they were; import again to finish.")
    lines.append("The tg-crawl folder was not changed. Next: press Get new messages (or run tgkit sync) "
                 "to get what is newer.")
    return "\n".join(lines)
