"""What the app (and the CLI) read: search hits, summaries, tasks, chats, threads.

Everything here READS the local copy. The only file it may change is
tasks.json, through tasks.TaskList, exactly as `tgkit tasks` does. It never
writes a chat folder: new messages come only from the guarded `tgkit sync`.

`answer()` is the app's offline mode: a parsed intent becomes a short reply
in the user's language plus result cards the UI draws.
"""

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config, digest
from . import tasks as taskmod
from .errors import UserError
from .intent import Intent
from .store import Store, fold, local_time, matcher, parse_date, parse_since, thread_of

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
            "link_base": store.link_base(d),
            **store.link_template(d),
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
        **store.link_info(d, r.get("id")),
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


# ------------------------------------------------------------------ today

TODAY_MAX = 8  # items shown per group; the rest is counted ("and N more"), never dropped silently
ASK_HOURS = 24  # how far back "people waiting for your answer" looks
_TODAY_CUE = re.compile(r"\b(hom nay|hnay|today|tonight|toi nay|sang nay|chieu nay|trua nay|trong ngay|"
                        r"cuoi ngay|eod|end of (?:the )?day)\b")
_TOMORROW_CUE = re.compile(r"\b(ngay mai|mai nhe|sang mai|chieu mai|toi mai|tomorrow)\b")
_QUESTION = re.compile(r"\?|\b(khong|ko|chua|duoc khong|the nao|bao gio|luc nao|o dau|can you|could you|"
                       r"would you|please|pls|nho|giup|gui|check)\b")
_TODAY_GROUPS = {
    "tasks": ("Việc của bạn", "Your open tasks"),
    "asks": ("Đang chờ bạn trả lời", "Waiting for your answer"),
    "today": ("Nhắc đến hôm nay", "Mentioned for today"),
}


def _today_date_cue(local_day) -> re.Pattern:
    """Today's date written the Vietnamese way (day first): 3/11, 03/11, 3-11-2026."""
    return re.compile(rf"(?<![\d/.-])0?{local_day.day}[/.-]0?{local_day.month}(?!\d)")


def _item(store: Store, d: Path, r: dict, why: str) -> dict:
    text = (r.get("text") or "").replace("\n", " ")
    return {"chat": store.title(d), "folder": d.name, "id": r.get("id"), "date": r.get("date"),
            "time": local_time(r.get("date")), "sender": store.sender_name(r.get("sender_id")),
            "text": text[:240] + ("..." if len(text) > 240 else ""), "why": why,
            **store.link_info(d, r.get("id"))}


def today(base: Path, store: Store, lang: str = "en", now: datetime | None = None) -> dict:
    """What do I need to do today. Only READS: tasks.json and the chats are never written
    (a task-like message not in the list yet is shown, not saved). Three groups, each
    filtered first and only then cut to TODAY_MAX, with `more` saying how many were left out:

      tasks  open tasks for me (tasks.json), and task-like messages to me from the last
             2 days not in the list yet; due today first
      asks   questions and requests to me from the last 24 hours with no later message
             from me in that chat (a private chat, a reply to me, or an @mention of me)
      today  messages that name today: "hôm nay", "today", today's date, or "ngày mai" /
             "tomorrow" written yesterday
    """
    now = now or datetime.now(timezone.utc)
    local_now = now.astimezone()
    day = local_now.date()
    start_today = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    start_yesterday = start_today - timedelta(days=1)
    cfg = config.load(base) if config.config_path(base).exists() else {}
    me = cfg.get("me") or {}
    my_id, my_user = me.get("id"), (me.get("username") or "").lower()
    date_cue = _today_date_cue(day)
    dirs = store.chat_dirs()
    seen: set = set()

    def mentions_today(text: str, when) -> bool:
        f = fold(text)
        if when is not None and when >= start_today and _TODAY_CUE.search(f):
            return True
        if when is not None and start_yesterday <= when < start_today and _TOMORROW_CUE.search(f):
            return True
        return bool(when is not None and when >= start_today - timedelta(days=7) and date_cue.search(text))

    # tasks: the list as saved, plus what a scan would add (read only)
    tl = taskmod.TaskList(base)
    task_items = []
    for t in tl.select("mine"):
        when = parse_date(t.get("date"))
        due_today = bool(t.get("due") and mentions_today(t.get("due") or "", now)) or bool(
            when and mentions_today(t.get("text") or "", when))
        item = {"chat": t.get("chat"), "folder": t.get("folder"), "id": t.get("msg_id"), "task": t.get("id"),
                "date": t.get("date"), "time": local_time(t.get("date")) if t.get("date") else None,
                "sender": t.get("sender"), "text": (t.get("text") or "")[:240], "due": t.get("due"),
                "due_today": due_today, "why": "task", "link": None, "link_kind": "none"}
        if t.get("folder") and (store.chats / t["folder"]).is_dir() and t.get("msg_id") is not None:
            item.update(store.link_info(store.chats / t["folder"], t.get("msg_id")))
        task_items.append(item)
        seen.add((t.get("folder"), t.get("msg_id")))
    for t in tl.find(store, dirs, now - timedelta(days=2), me):
        if not t.get("for_me") or (t["folder"], t["msg_id"]) in seen:
            continue
        d = store.chats / t["folder"]
        when = parse_date(t.get("date"))
        task_items.append({**_item(store, d, {"id": t["msg_id"], "date": t["date"], "text": t["text"],
                                               "sender_id": None}, "task-new"),
                           "sender": t.get("sender"), "due": t.get("due"),
                           "due_today": bool(when and mentions_today(t.get("text") or "", when))})
        seen.add((t["folder"], t["msg_id"]))
    task_items.sort(key=lambda i: (not i.get("due_today"), -(parse_date(i.get("date")) or now).timestamp()))

    asks, mentioned = [], []
    since_ask = now - timedelta(hours=ASK_HOURS)
    for d in dirs:
        rs = store.rows(d)
        dm = store.meta(d).get("type") == "User"
        by_id = {r.get("id"): r for r in rs}
        my_last = max((parse_date(r.get("date")) for r in rs
                       if my_id is not None and r.get("sender_id") == my_id and parse_date(r.get("date"))),
                      default=None)
        for r in rs:
            when = parse_date(r.get("date"))
            if when is None or when < start_yesterday - timedelta(days=6):
                continue
            key = (d.name, r.get("id"))
            if key in seen:
                continue
            text = r.get("text") or ""
            mine = my_id is not None and r.get("sender_id") == my_id
            if (not mine and my_id is not None and when >= since_ask and (my_last is None or my_last < when)
                    and _QUESTION.search(fold(text))):
                parent = by_id.get(r.get("reply_to_msg_id")) or {}
                to_me = (dm or parent.get("sender_id") == my_id
                         or bool(my_user and f"@{my_user}" in text.lower()))
                if to_me:
                    asks.append(_item(store, d, r, "ask"))
                    seen.add(key)
                    continue
            if text and mentions_today(text, when):
                mentioned.append(_item(store, d, r, "today"))
                seen.add(key)
    newest = lambda i: -(parse_date(i.get("date")) or now).timestamp()  # noqa: E731
    asks.sort(key=newest)
    mentioned.sort(key=newest)

    groups = []
    for key, items in (("tasks", task_items), ("asks", asks), ("today", mentioned)):
        vi, en = _TODAY_GROUPS[key]
        groups.append({"key": key, "title": vi if lang == "vi" else en, "total": len(items),
                       "items": [i for i in items[:TODAY_MAX]], "more": max(0, len(items) - TODAY_MAX)})
    state = store.sync_state()
    stale = [store.title(d) for d in dirs if store.is_stale(store.synced_at(d, state))]
    return {"kind": "today", "date": day.isoformat(), "me": me.get("name"), "known_me": my_id is not None,
            "groups": groups, "counts": {g["key"]: g["total"] for g in groups},
            "total": sum(g["total"] for g in groups), "stale": stale}


def today_card(data: dict) -> dict:
    return {"type": "today", "date": data.get("date"), "groups": data.get("groups") or [],
            "total": data.get("total", 0), "stale": data.get("stale") or []}


def today_text(data: dict, lang: str) -> str:
    c = data.get("counts") or {}
    t, a, m = c.get("tasks", 0), c.get("asks", 0), c.get("today", 0)
    if not data.get("total"):
        text = _t(lang, "Hôm nay chưa thấy việc nào cần bạn làm trong các nhóm đã sao chép.",
                  "Nothing for you to do today in the chats copied so far.")
    else:
        parts_vi, parts_en = [], []
        if t:
            parts_vi.append(f"{t} việc của bạn")
            parts_en.append(f"{t} open task{'s' if t != 1 else ''}")
        if a:
            parts_vi.append(f"{a} tin đang chờ bạn trả lời")
            parts_en.append(f"{a} message{'s' if a != 1 else ''} waiting for your answer")
        if m:
            parts_vi.append(f"{m} tin nhắc đến hôm nay")
            parts_en.append(f"{m} mentioned for today")
        text = _t(lang, "Hôm nay: " + ", ".join(parts_vi) + ".", "Today: " + ", ".join(parts_en) + ".")
    if not data.get("known_me"):
        text += _t(lang, " Chưa biết tài khoản của bạn, nên chưa thể biết ai đang chờ bạn trả lời.",
                   " Your account is not known yet, so messages waiting for your answer cannot be found.")
    if data.get("stale"):
        text += _t(lang, " Vài nhóm chưa cập nhật hơn 12 giờ; bấm Lấy tin mới.",
                   " Some chats were last updated more than 12 hours ago; press Get new messages.")
    return text


# ------------------------------------------------------------------ thread


def thread(store: Store, folder: str, msg_id: int, around: int = 3) -> list[dict]:
    """The reply thread of one message; when it has no replies, the messages around it."""
    d = store.chats / folder
    if folder not in {x.name for x in store.chat_dirs()}:
        raise UserError("That chat is not in Telegram Assistant.", "Pick a message from the results again.")
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


def source_of(command: str) -> dict | None:
    """How a `tgkit search|summary|tasks ...` command was asked, so Save as file can run
    it again for every result. None when the command cannot be read back."""
    import shlex  # noqa: PLC0415

    try:
        words = shlex.split(command or "")
    except ValueError:
        return None
    if words[:1] != ["tgkit"] or len(words) < 2:
        return None
    sub, rest = words[1], words[2:]
    opts, pos, i = {}, [], 0
    while i < len(rest):
        w = rest[i]
        if w in ("--chat", "--since", "--limit", "--context") and i + 1 < len(rest):
            opts[w[2:]] = rest[i + 1]
            i += 2
            continue
        if not w.startswith("-"):
            pos.append(w)
        i += 1
    if sub in ("search", "find") and pos:
        return {"kind": "search", "query": pos[0], "chat": opts.get("chat"), "since": opts.get("since")}
    if sub in ("summary", "digest"):
        return {"kind": "summary", "chat": pos[0] if pos else None, "since": opts.get("since")}
    if sub in ("tasks", "task", "todo"):
        which = pos[0] if pos and pos[0] in ("mine", "all", "open") else "open"
        return {"kind": "tasks", "which": which, "chat": opts.get("chat"), "since": opts.get("since")}
    return None


def card_from_json(command: str, data) -> dict | None:
    """A card from what a `tgkit ... --json` command printed (Claude mode)."""
    if not isinstance(data, dict):
        return None
    card = None
    if "results" in data and "query" in data:
        card = hits_card(data)
    elif "chats" in data and "window" in data:
        card = summary_card(data)
    elif data.get("kind") == "today" and isinstance(data.get("groups"), list):
        card = today_card(data)
    elif isinstance(data.get("tasks"), list):
        which = "mine" if " mine" in command else "all" if " all" in command else "open"
        card = tasks_card(data["tasks"], which)
    if card is not None:
        src = source_of(command)
        if src:
            card["source"] = src
    return card


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
        cards = [{**hits_card(data, it.chat_title, period, stale),
                  "source": {"kind": "search", "query": it.query, "chat": it.chat, "since": it.since}}] if n else []
        return {"text": text + note, "cards": cards, "action": None if n or not stale else "sync"}

    if it.kind == "summary":
        dg = digest.build(store, store.match(it.chat), it.since)
        label = period or _t(lang, "7 ngày qua", "the last 7 days")
        card = summary_card(dg, label)
        card["source"] = {"kind": "summary", "chat": it.chat, "since": it.since}
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
        cards = [{**tasks_card(rows, it.which),
                  "source": {"kind": "tasks", "which": it.which, "chat": it.chat, "since": it.since}}] if rows else []
        return {"text": head, "cards": cards, "action": None}

    if it.kind == "today":  # offline, zero tokens: the same data `tgkit today --json` prints
        data = today(base, store, lang)
        return {"text": today_text(data, lang), "cards": [today_card(data)] if data["total"] else [],
                "action": None}

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
