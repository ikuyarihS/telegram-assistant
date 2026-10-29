"""An offline, extractive summary of a time window. No AI, no network.

It picks out, per chat: how many messages, who wrote, the busiest reply
threads, questions, possible decisions, task-like requests, links and files.
`--json` gives the same as data, shaped for Claude or another AI to read.

Every question and decision carries the message it REPLIES to. "Em đồng ý"
means nothing until you know what was asked; a summary must never turn a
reply into agreement with something its author did not answer.
"""

import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from . import tasks as taskmod
from .store import Store, describe_since, fold, local_time, parse_date, parse_since, thread_root

_URL = re.compile(r"https?://[^\s<>\"']+")
_QUESTION_ENDINGS = ("khong a", "ko a", "chua a", "khong", "ko", "chua", "nhi", "the nao",
                     "bao gio", "duoc khong", "dc ko", "duoc ko", "khong nhi", "sao")
_DECIDE_EN = re.compile(
    r"\b(decided|decision|agreed|approved|confirmed|go with|finali[sz]ed|signed off)\b", re.IGNORECASE)
_DECIDE_VI = re.compile(r"\b(chot|thong nhat|dong y|duyet|phe duyet|quyet dinh|xac nhan)\b")

DEFAULT_WINDOW = "7d"
TEXT_MAX = 500


def is_question(text: str) -> bool:
    if "?" in (text or ""):
        return True
    f = re.sub(r"[\s.!,]+$", "", fold(text))
    return any(f == e or f.endswith(" " + e) for e in _QUESTION_ENDINGS)


def is_decision(text: str) -> bool:
    return bool(_DECIDE_EN.search(text or "") or _DECIDE_VI.search(fold(text)))


def _clip(text: str | None, n: int = TEXT_MAX) -> str:
    text = text or ""
    return text if len(text) <= n else text[:n] + "..."


def _item(store: Store, r: dict, by_id: dict) -> dict:
    out = {
        "id": r.get("id"),
        "date": r.get("date"),
        "time": local_time(r.get("date")),
        "sender": store.sender_name(r.get("sender_id")),
        "text": _clip(r.get("text")),
        "in_reply_to": None,
    }
    rid = r.get("reply_to_msg_id")
    if rid:
        parent = by_id.get(rid)
        if parent:
            out["in_reply_to"] = {
                "id": rid,
                "sender": store.sender_name(parent.get("sender_id")),
                "text": _clip(parent.get("text"), 300),
            }
        else:
            out["in_reply_to"] = {"id": rid, "sender": None, "text": None,
                                  "note": "not in the local copy"}
    return out


def chat_digest(store: Store, d: Path, since: datetime | None, state: dict) -> dict:
    rs = store.rows(d)
    by_id = {r.get("id"): r for r in rs if r.get("id") is not None}
    window = [r for r in rs if since is None or ((parse_date(r.get("date")) or since) >= since)]
    synced = store.synced_at(d, state)
    out = {
        "title": store.title(d),
        "folder": d.name,
        "messages": len(window),
        "first": window[0].get("date") if window else None,
        "last": window[-1].get("date") if window else None,
        "synced_at": synced.replace(microsecond=0).isoformat() if synced else None,
        "stale": store.is_stale(synced),
        "people": [],
        "threads": [],
        "questions": [],
        "decisions": [],
        "tasks": [],
        "links": [],
        "files": [],
    }
    people = Counter(store.sender_name(r.get("sender_id")) for r in window)
    out["people"] = [{"name": n, "messages": c} for n, c in people.most_common()]

    groups: dict = {}
    for r in window:
        if r.get("id") is None:
            continue
        groups.setdefault(thread_root(by_id, r["id"]), []).append(r)
    threads = [(root, members) for root, members in groups.items() if len(members) >= 2]
    threads.sort(key=lambda x: (-len(x[1]), -(parse_date(x[1][-1].get("date")) or
                                              datetime.min.replace(tzinfo=timezone.utc)).timestamp()))
    for root, members in threads[:5]:
        root_row = by_id.get(root, members[0])
        out["threads"].append({
            "root": _item(store, root_row, by_id),
            "messages": len(members),
            "people": sorted({store.sender_name(m.get("sender_id")) for m in members}),
            "last": members[-1].get("date"),
        })

    for r in window:
        text = r.get("text") or ""
        if text and is_question(text):
            out["questions"].append(_item(store, r, by_id))
        if text and is_decision(text):
            out["decisions"].append(_item(store, r, by_id))
        cue = taskmod.detect(text)
        if cue:
            out["tasks"].append({**_item(store, r, by_id), **cue})
        for url in _URL.findall(text):
            url = url.rstrip(".,;:!?)]}")
            out["links"].append({"url": url, "id": r.get("id"), "date": r.get("date"),
                                 "sender": store.sender_name(r.get("sender_id"))})
        m = r.get("media") or {}
        if m:
            path = r.get("media_path")
            out["files"].append({
                "name": m.get("file_name") or ("photo" if m.get("kind") == "photo" else m.get("kind")),
                "path": str(store.chats / path) if path else None,
                "id": r.get("id"),
                "date": r.get("date"),
                "sender": store.sender_name(r.get("sender_id")),
            })
    return out


def build(store: Store, dirs: list[Path], since_text: str | None) -> dict:
    since = parse_since(since_text, DEFAULT_WINDOW)
    state = store.sync_state()
    chats, quiet = [], []
    for d in dirs:
        c = chat_digest(store, d, since, state)
        if c["messages"]:
            chats.append(c)
        else:
            quiet.append({"title": c["title"], "synced_at": c["synced_at"], "stale": c["stale"]})
    chats.sort(key=lambda c: -c["messages"])
    return {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "window": {"since": since.isoformat() if since else None,
                   "label": describe_since(since_text, DEFAULT_WINDOW)},
        "total_messages": sum(c["messages"] for c in chats),
        "chats": chats,
        "quiet_chats": quiet,
        "note": "Extractive only: quotes, not interpretations. Decisions are POSSIBLE "
                "decisions; read in_reply_to before treating one as agreement.",
    }


def _one(it: dict, indent: str = "  - ") -> list[str]:
    text = (it.get("text") or "").replace("\n", " / ")
    lines = [f"{indent}[{it['time']}] {it['sender']}: {text}   (#{it['id']})"]
    ir = it.get("in_reply_to")
    if ir:
        if ir.get("text") is not None:
            parent = (ir["text"] or "").replace("\n", " / ")
            lines.append(f"      replying to {ir['sender']}: \"{parent}\" (#{ir['id']})")
        else:
            lines.append(f"      replying to #{ir['id']} (not in the local copy)")
    return lines


def render(dg: dict) -> str:
    out = []
    since = dg["window"]["since"]
    head = f"Summary: {dg['window']['label']}"
    if since:
        head += f" (from {local_time(since)})"
    head += f", {dg['total_messages']} message(s) in {len(dg['chats'])} chat(s)."
    out.append(head)
    stale = [c for c in dg["chats"] + dg["quiet_chats"] if c["stale"]]
    if stale:
        out.append("NOTE: some chats were last updated more than 12 hours ago, so newer "
                   "messages may be missing. Run: tgkit sync")
    for c in dg["chats"]:
        out.append("")
        out.append(f"== {c['title']} ==  {c['messages']} message(s), "
                   f"{local_time(c['first'])} to {local_time(c['last'])}")
        upd = local_time(c["synced_at"]) if c["synced_at"] else "never"
        out.append(f"Local copy updated: {upd}" +
                   ("  (old: run tgkit sync for newer messages)" if c["stale"] else ""))
        out.append("People: " + ", ".join(f"{p['name']} ({p['messages']})" for p in c["people"]))
        if c["threads"]:
            out.append("Busiest conversations:")
            for t in c["threads"]:
                root = (t["root"]["text"] or "(no text)").replace("\n", " / ")[:160]
                out.append(f"  - {t['messages']} messages, {', '.join(t['people'])}: "
                           f"\"{root}\" (#{t['root']['id']})")
        if c["questions"]:
            out.append("Questions:")
            for it in c["questions"]:
                out.extend(_one(it))
        if c["decisions"]:
            out.append("Possible decisions (check what each one replies to before relying on it):")
            for it in c["decisions"]:
                out.extend(_one(it))
        if c["tasks"]:
            out.append("Requests and to-dos:")
            for it in c["tasks"]:
                lines = _one(it)
                if it.get("due"):
                    lines[0] += f"  (due: {it['due']})"
                out.extend(lines)
        if c["links"]:
            out.append("Links:")
            for ln in c["links"]:
                out.append(f"  - {ln['url']}  ({ln['sender']}, #{ln['id']})")
        if c["files"]:
            out.append("Files:")
            for f in c["files"]:
                out.append(f"  - {f['name']}  ({f['sender']}, #{f['id']})")
    if dg["quiet_chats"]:
        out.append("")
        out.append("No messages in this period: " + ", ".join(q["title"] for q in dg["quiet_chats"]))
    out.append("")
    out.append("Tip: tgkit summary --since 30d for a longer period; add --json for Claude.")
    return "\n".join(out)
