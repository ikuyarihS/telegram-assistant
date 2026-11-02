"""What the app (and the CLI) read: search hits, summaries, tasks, chats, threads.

Everything here READS the local copy. The only file it may change is
tasks.json, through tasks.TaskList, exactly as `tgkit tasks` does. It never
writes a chat folder: new messages come only from the guarded `tgkit sync`.

`answer()` is the app's offline mode: a parsed intent becomes a short reply
in the user's language plus result cards the UI draws.
"""

import re
from pathlib import Path

from . import config, digest
from . import tasks as taskmod
from .errors import UserError
from .intent import Intent
from .store import Store, local_time, matcher, parse_date, parse_since, thread_of

SEARCH_LIMIT = 30
CARD_LIST_MAX = 8


# ------------------------------------------------------------------ chats


def chat_list(store: Store) -> list[dict]:
    """Every followed chat with its size and freshness. Reads one line per message."""
    state = store.sync_state()
    out = []
    for d in store.chat_dirs():
        count, newest = 0, None
        try:
            with (d / "messages.jsonl").open(encoding="utf-8") as fh:
                for line in fh:
                    if line.strip():
                        count += 1
                        if newest is None:
                            m = re.search(r'"date":\s*"([^"]+)"', line)
                            newest = m.group(1) if m else None
        except OSError:
            pass
        when = store.synced_at(d, state)
        out.append({
            "folder": d.name,
            "title": store.title(d),
            "messages": count,
            "last_message": newest,
            "synced_at": when.replace(microsecond=0).isoformat() if when else None,
            "stale": store.is_stale(when),
        })
    out.sort(key=lambda c: c["title"].casefold())
    return out


def chat_names(store: Store) -> list[tuple[str, str]]:
    return [(d.name, store.title(d)) for d in store.chat_dirs()]


# ------------------------------------------------------------------ search


def find(store: Store, query: str, chat: str | None = None, since: str | None = None,
         case: bool = False, words: bool = False) -> list[tuple]:
    """(row, folder, index, rows) for every hit, newest first. The CLI's search uses this too.

    `words=True` matches messages holding every word, in any order.
    """
    if words:
        tests = [matcher(w, case) for w in query.split()]

        def test(text):
            return all(t(text) for t in tests)
    else:
        test = matcher(query, case)
    cutoff = parse_since(since) if since else None
    out = []
    for d in store.match(chat):
        rs = store.rows(d)
        for i, r in enumerate(rs):
            if cutoff and (parse_date(r.get("date")) or cutoff) < cutoff:
                continue
            hay = r.get("text") or ""
            m = r.get("media") or {}
            if m.get("file_name"):
                hay += " " + m["file_name"]
            if test(hay):
                out.append((r, d, i, rs))
    out.sort(key=lambda h: h[0].get("date") or "", reverse=True)
    return out


def hit_dict(store: Store, r: dict, d: Path) -> dict:
    """One search hit, in the shape `tgkit search --json` prints."""
    return {
        "chat": store.title(d), "folder": d.name, "id": r.get("id"), "date": r.get("date"),
        "sender": store.sender_name(r.get("sender_id")), "text": r.get("text") or "",
        "file": (r.get("media") or {}).get("file_name"), "reply_to": r.get("reply_to_msg_id"),
    }


def search(store: Store, query: str, chat: str | None = None, since: str | None = None,
           limit: int = SEARCH_LIMIT) -> dict:
    """Phrase first; when a phrase of several words finds nothing, every word in any order."""
    hits = find(store, query, chat, since)
    match = "phrase"
    if not hits and len(query.split()) > 1:
        hits = find(store, query, chat, since, words=True)
        match = "words"
    shown = hits[:limit] if limit else hits
    return {"query": query, "total": len(hits), "match": match,
            "results": [hit_dict(store, r, d) for r, d, _, _ in shown]}


# ------------------------------------------------------------------ tasks


def scan_tasks(base: Path, store: Store, chat: str | None = None, since: str | None = None):
    """Find new tasks in the messages and save them, as `tgkit tasks` does. Returns (list, found)."""
    tl = taskmod.TaskList(base)
    dirs = store.match(chat) if chat else store.chat_dirs()
    found = 0
    if dirs:
        # Read the messages without the lock; add what is new under it, to the file
        # as it is now (a Done click may have saved it meanwhile).
        new = tl.find(store, dirs, taskmod.window_start(since), config.load(base).get("me"))
        if new:
            with tl.change():
                found = tl.merge(new)
    return tl, found


def select_tasks(tl, store: Store, which: str, chat: str | None = None,
                 since: str | None = None) -> list[dict]:
    """The tasks to list: `which` ones, only from the chats `chat` names and only since
    `since` when given. The scan alone limits only where new tasks are looked for."""
    rows = tl.select(which)
    if chat:
        folders = {d.name for d in store.match(chat)}
        rows = [t for t in rows if t.get("folder") in folders]
    cut = parse_since(since) if since else None
    if cut:
        rows = [t for t in rows if (parse_date(t.get("date")) or cut) >= cut]
    return rows


def set_task(base: Path, ident, status: str) -> dict:
    if status not in ("open", "done", "dropped"):
        raise UserError(f"'{status}' is not a task state.", "Use open, done or dropped.")
    tl = taskmod.TaskList(base)
    with tl.change():
        t = tl.set_status(str(ident), status)
    return t


# ------------------------------------------------------------------ thread


def thread(store: Store, folder: str, msg_id: int, around: int = 3) -> list[dict]:
    """The reply thread of one message; when it has no replies, the messages around it."""
    d = store.chats / folder
    if folder not in {x.name for x in store.chat_dirs()}:
        raise UserError("That chat is not in TG Kit.", "Pick a message from the results again.")
    rs = store.rows(d)
    picked = thread_of(rs, msg_id)
    if len(picked) < 2:
        idx = next((i for i, r in enumerate(rs) if r.get("id") == msg_id), None)
        if idx is None:
            return []
        picked = rs[max(0, idx - around): idx + around + 1]
    return [{**hit_dict(store, r, d), "time": local_time(r.get("date")), "anchor": r.get("id") == msg_id}
            for r in picked]


# ------------------------------------------------------------------ cards


def _with_time(item: dict) -> dict:
    return {**item, "time": local_time(item.get("date"))} if item.get("date") else item


def hits_card(data: dict, chat_title: str | None = None, since_label: str | None = None,
              stale: list | None = None) -> dict:
    return {
        "type": "hits",
        "query": data.get("query"),
        "total": data.get("total", len(data.get("results") or [])),
        "match": data.get("match", "phrase"),
        "chat": chat_title,
        "since": since_label,
        "stale": stale or [],
        "items": [_with_time(r) for r in data.get("results") or []],
    }


def summary_card(dg: dict, label: str | None = None) -> dict:
    def few(items, n=CARD_LIST_MAX):
        return [_with_time(i) for i in (items or [])[:n]]

    chats = []
    for c in dg.get("chats") or []:
        chats.append({
            "title": c.get("title"), "folder": c.get("folder"), "messages": c.get("messages"),
            "first": local_time(c.get("first")) if c.get("first") else None,
            "last": local_time(c.get("last")) if c.get("last") else None,
            "synced_at": local_time(c.get("synced_at")) if c.get("synced_at") else None,
            "stale": bool(c.get("stale")),
            "people": (c.get("people") or [])[:6],
            "threads": [{"root": _with_time(t.get("root") or {}), "messages": t.get("messages"),
                         "people": t.get("people")} for t in (c.get("threads") or [])[:3]],
            "questions": few(c.get("questions")),
            "decisions": few(c.get("decisions")),
            "tasks": few(c.get("tasks")),
            "links": few(c.get("links"), 5),
            "files": few(c.get("files"), 5),
        })
    quiet = dg.get("quiet_chats") or []
    return {
        "type": "summary",
        "label": label or (dg.get("window") or {}).get("label"),
        "total": dg.get("total_messages", 0),
        "chats": chats,
        "quiet": [q.get("title") for q in quiet],
        "stale": [c["title"] for c in chats if c["stale"]] + [q.get("title") for q in quiet if q.get("stale")],
    }


def tasks_card(rows: list[dict], which: str = "open") -> dict:
    return {"type": "tasks", "which": which, "items": [{
        "id": t.get("id"), "text": t.get("text") or "", "chat": t.get("chat"), "folder": t.get("folder"),
        "msg_id": t.get("msg_id"), "sender": t.get("sender"), "date": t.get("date"),
        "time": local_time(t.get("date")) if t.get("date") else None, "due": t.get("due"),
        "for_me": bool(t.get("for_me")), "status": t.get("status"), "source": t.get("source"),
    } for t in rows]}


def card_from_json(command: str, data) -> dict | None:
    """A card from what a `tgkit ... --json` command printed (Claude mode)."""
    if not isinstance(data, dict):
        return None
    if "results" in data and "query" in data:
        return hits_card(data)
    if "chats" in data and "window" in data:
        return summary_card(data)
    if isinstance(data.get("tasks"), list):
        which = "mine" if " mine" in command else "all" if " all" in command else "open"
        return tasks_card(data["tasks"], which)
    return None


# ------------------------------------------------------------------ offline answers

_SINCE_VI = {"today": "hôm nay", "yesterday": "từ hôm qua", "all": "mọi lúc"}
_SINCE_EN = {"today": "today", "yesterday": "since yesterday", "all": "all time"}


def since_label(since: str | None, lang: str) -> str | None:
    if not since:
        return None
    table = _SINCE_VI if lang == "vi" else _SINCE_EN
    if since in table:
        return table[since]
    m = re.fullmatch(r"(\d+)([dwh])", since)
    if m:
        n, u = int(m.group(1)), m.group(2)
        if lang == "vi":
            return f"{n} {'ngày' if u == 'd' else 'tuần' if u == 'w' else 'giờ'} qua"
        unit = {"d": "day", "w": "week", "h": "hour"}[u] + ("s" if n != 1 else "")
        return f"the last {n} {unit}"
    return (f"từ {since}" if lang == "vi" else f"since {since}")


EXAMPLES = {
    "vi": ["Tuần này các nhóm bàn gì?", "Việc của tôi", "tìm biên bản họp", "Hôm nay có gì mới?"],
    "en": ["What happened this week?", "My open tasks", "find meeting minutes", "Summarize the last 30 days"],
}


def _t(lang: str, vi: str, en: str) -> str:
    return vi if lang == "vi" else en


def answer(base: Path, store: Store, it: Intent) -> dict:
    """{"text", "cards", "action"} for one parsed message. Raises UserError in plain words."""
    lang = it.lang
    where = f" {_t(lang, 'trong', 'in')} {it.chat_title}" if it.chat_title else ""
    period = since_label(it.since, lang)
    when = f", {period}" if period else ""
    note = ""
    if it.ambiguous_chats:
        names = ", ".join(it.ambiguous_chats)
        note = _t(lang, f" (Tên nhóm khớp nhiều nhóm: {names}. Mình tìm trong tất cả.)",
                  f" (That chat name matches several chats: {names}. I looked in all of them.)")

    if it.kind == "search":
        if not it.query:
            return {"text": _t(lang, "Bạn muốn tìm gì? Ví dụ: tìm biên bản họp",
                               "What should I look for? For example: find meeting minutes"),
                    "cards": [], "action": None}
        data = search(store, it.query, it.chat, it.since)
        dirs = store.match(it.chat)
        state = store.sync_state()
        stale = [store.title(d) for d in dirs if store.is_stale(store.synced_at(d, state))]
        n = data["total"]
        if n:
            text = _t(lang, f"Tìm thấy {n} tin nhắn có “{it.query}”{where}{when}.",
                      f"Found {n} message{'s' if n != 1 else ''} with “{it.query}”{where}{when}.")
            if data["match"] == "words":
                text += _t(lang, " Không có nguyên cụm, đây là các tin có đủ các từ.",
                           " The exact phrase is not there; these hold every word.")
            if n > len(data["results"]):
                text += _t(lang, f" Đây là {len(data['results'])} tin mới nhất.",
                           f" Showing the newest {len(data['results'])}.")
        else:
            text = _t(lang, f"Không thấy tin nhắn nào có “{it.query}”{where}{when}. "
                            "Thử ít từ hơn, hoặc gõ không dấu.",
                      f"No messages with “{it.query}”{where}{when}. Try fewer words.")
        cards = [hits_card(data, it.chat_title, period, stale)] if n else []
        return {"text": text + note, "cards": cards, "action": None if n or not stale else "sync"}

    if it.kind == "summary":
        dg = digest.build(store, store.match(it.chat), it.since)
        label = period or _t(lang, "7 ngày qua", "the last 7 days")
        card = summary_card(dg, label)
        k = len(card["chats"])
        if card["total"]:
            text = _t(lang, f"{label[:1].upper() + label[1:]}: {card['total']} tin nhắn trong {k} nhóm{where}.",
                      f"{label[:1].upper() + label[1:]}: {card['total']} messages in {k} chat"
                      f"{'s' if k != 1 else ''}{where}.")
        else:
            text = _t(lang, f"Không có tin nhắn nào{where} trong {label}.", f"No messages{where} in {label}.")
        if card["stale"]:
            text += _t(lang, " Vài nhóm chưa cập nhật hơn 12 giờ; bấm Đồng bộ để lấy tin mới.",
                       " Some chats were last updated more than 12 hours ago; press Sync for new messages.")
        return {"text": text + note, "cards": [card], "action": None}

    if it.kind == "tasks":
        tl, found = scan_tasks(base, store, it.chat, it.since)
        rows = select_tasks(tl, store, it.which, it.chat, it.since)
        n = len(rows)
        head = {
            "open": _t(lang, f"Bạn có {n} việc đang mở.", f"You have {n} open task{'s' if n != 1 else ''}."),
            "mine": _t(lang, f"Có {n} việc dành cho bạn.", f"{n} open task{'s' if n != 1 else ''} for you."),
            "all": _t(lang, f"Tất cả: {n} việc.", f"All tasks: {n}."),
        }[it.which]
        if found:
            head += _t(lang, f" Mới tìm thấy {found} việc trong tin nhắn.", f" {found} new found in your messages.")
        return {"text": head, "cards": [tasks_card(rows, it.which)] if rows else [], "action": None}

    if it.kind == "chats":
        items = chat_list(store)
        text = _t(lang, f"Bạn đang theo dõi {len(items)} nhóm.", f"You follow {len(items)} chats.")
        return {"text": text, "cards": [{"type": "chats", "items": items}], "action": None}

    if it.kind == "sync":
        return {"text": _t(lang, "Đang lấy tin mới từ Telegram.", "Getting new messages from Telegram."),
                "cards": [], "action": "sync"}

    return {"text": _t(lang, "Mình tìm tin nhắn, tóm tắt các nhóm và liệt kê việc cần làm. Thử hỏi:",
                       "I can find messages, summarize your chats and list your tasks. Try:"),
            "cards": [{"type": "examples", "items": EXAMPLES[lang]}], "action": None}
