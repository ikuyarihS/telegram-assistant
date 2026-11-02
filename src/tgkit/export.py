"""Save a conversation, a search, a summary or the task list as a file to share.

Formats: .docx (the default: files people share go out as Word documents),
.xlsx and .csv (the table: messages found, tasks, ...), and .md. Every writer is
plain Python (zip + XML), so nothing extra has to be installed, and every
string stays Unicode end to end: Vietnamese diacritics come out intact.

Files go to the folder chosen in the app's Settings, or Documents/TG Kit. A
file is never overwritten: a second export gets " (2)" in its name.

It only READS the local copy: no export changes a chat, a task or a setting.
"Save file" copies an attachment that sync already put in the chat's media
folder; it never downloads anything.
"""

import csv
import io
import os
import re
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

from . import config, digest, engine
from . import tasks as taskmod
from .errors import UserError
from .store import Store, is_dead_link, local_time

FORMATS = ("docx", "xlsx", "csv", "md")
KINDS = ("conversation", "search", "summary", "tasks", "card")

_L = {
    "en": {
        "search": "Search", "summary": "Summary", "tasks": "Tasks", "conversation": "Conversation",
        "chats": "Chats", "date": "Date", "chat": "Chat", "from": "From", "message": "Message",
        "file": "File", "no": "No.", "task": "Task", "status": "Status", "due": "Due",
        "for_you": "For you", "notes": "Notes", "time": "Time", "who": "Who", "text": "Text",
        "kind": "Kind", "you": "You", "yes": "yes", "messages": "messages", "words": "every word, in any order",
        "exported": "Exported from TG Kit on", "in_chat": "Chats matching", "period": "Period",
        "people": "People", "threads": "Busiest conversations", "questions": "Questions",
        "decisions": "Possible decisions (check what each one replies to)", "requests": "Requests and to-dos",
        "links": "Links", "files": "Files", "replying": "replying to", "not_local": "a message not in the local copy",
        "quiet": "No messages in this period", "stale": "may be out of date", "updated": "updated",
        "open": "open", "done": "done", "dropped": "dropped", "nothing": "Nothing to show.",
        "which_open": "Open tasks", "which_mine": "Open tasks for you", "which_all": "All tasks",
        "updated_col": "Updated", "count": "Messages", "link": "Link", "open_tg": "Open in Telegram",
        "open_chat_tg": "Open the chat in Telegram", "find_tg": "Open Telegram and find {}",
    },
    "vi": {
        "search": "Tìm kiếm", "summary": "Tóm tắt", "tasks": "Việc cần làm", "conversation": "Cuộc trò chuyện",
        "chats": "Nhóm", "date": "Ngày", "chat": "Nhóm", "from": "Người gửi", "message": "Tin nhắn",
        "file": "Tệp", "no": "Số", "task": "Việc", "status": "Trạng thái", "due": "Hạn",
        "for_you": "Của bạn", "notes": "Ghi chú", "time": "Thời gian", "who": "Ai", "text": "Nội dung",
        "kind": "Loại", "you": "Bạn", "yes": "có", "messages": "tin nhắn", "words": "đủ các từ, không theo thứ tự",
        "exported": "Xuất từ TG Kit lúc", "in_chat": "Nhóm khớp với", "period": "Khoảng thời gian",
        "people": "Người nhắn", "threads": "Trao đổi nhiều nhất", "questions": "Câu hỏi",
        "decisions": "Có thể đã chốt (xem tin được trả lời trước khi dùng)", "requests": "Yêu cầu và việc cần làm",
        "links": "Liên kết", "files": "Tệp", "replying": "trả lời", "not_local": "một tin không có trong bản sao",
        "quiet": "Không có tin trong khoảng này", "stale": "có thể đã cũ", "updated": "cập nhật",
        "open": "đang mở", "done": "xong", "dropped": "bỏ", "nothing": "Không có gì để hiển thị.",
        "which_open": "Việc đang mở", "which_mine": "Việc của bạn", "which_all": "Tất cả việc",
        "updated_col": "Cập nhật", "count": "Số tin", "link": "Liên kết", "open_tg": "Mở trên Telegram",
        "open_chat_tg": "Mở cuộc trò chuyện trên Telegram", "find_tg": "Mở Telegram và tìm {}",
    },
}


_LINK_HEADS = {L["link"] for L in _L.values()}
# the link_note column of a CSV: fixed values, so a sheet can filter on them
LINK_NOTES = {"message": "message", "chat": "opens the chat", "none": "no link"}


def _labels(lang: str) -> dict:
    return _L["vi" if (lang or "").lower().startswith("vi") else "en"]


def _stamp() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")


def _lt(value) -> str:
    return local_time(value) if value else ""


# ------------------------------------------------------------------ documents
# A document: {"title", "meta": [lines], "blocks": [...], "table": (headers, rows) | None}
# blocks: {"h": 2, "text"}, {"p": text}, {"meta": text}, {"bullets": [text]}, {"table": (headers, rows)}


def _doc(title: str, L: dict, meta: list[str] | None = None) -> dict:
    return {"title": title, "meta": [*(meta or []), f"{L['exported']} {_stamp()}"], "blocks": [], "table": None}


def _link(url, L: dict, kind: str | None = None, name: str | None = None) -> dict:
    """A table cell that opens the message in Telegram (a hyperlink in Word and Excel).
    A private chat or a basic group has no message links: with a username its cell
    opens the chat; without one there is no link, the cell says to find the chat.
    "note" is the link_note column of a CSV (the same words in every language)."""
    if not url or is_dead_link(url):
        return {"text": L["find_tg"].format(name) if name else "", "url": None, "note": LINK_NOTES["none"]}
    if kind is None:
        kind = "message" if str(url).startswith("https://t.me/") else "chat"
    if kind == "chat":
        return {"text": L["open_chat_tg"], "url": url, "note": LINK_NOTES["chat"]}
    return {"text": L["open_tg"], "url": url, "note": LINK_NOTES["message"]}


def _row_link(store: Store | None, folder, msg_id, L: dict, have=None, kind=None, name=None):
    """The link cell of one row. A chat still in TG Kit: its link as it is now (a
    saved card can hold a link an earlier build made); else the row's own link,
    never a form Telegram Desktop cannot open."""
    d = store.chats / str(folder) if store is not None and folder else None
    if d is not None and (d / "channel.json").exists():
        info = store.link_info(d, msg_id)
        have, kind = info["link"], info["link_kind"]
        if not have and not name:
            name = store.title(d)
    elif is_dead_link(have):
        have, kind = None, "none"
    return _link(have, L, kind, name)


def _hits_table(items: list[dict], L: dict, store: Store | None = None):
    head = [L["date"], L["chat"], L["from"], L["message"], L["file"], L["link"]]
    rows = [[_lt(h.get("date")), h.get("chat") or "", h.get("sender") or "", h.get("text") or "",
             h.get("file") or "", _row_link(store, h.get("folder"), h.get("id"), L, h.get("link"), h.get("link_kind"),
                                            h.get("chat"))]
            for h in items]
    return head, rows


def _task_status(t: dict, L: dict) -> str:
    return L.get(t.get("status") or "open", t.get("status") or "")


def _tasks_table(rows: list[dict], L: dict, store: Store | None = None):
    head = [L["no"], L["task"], L["status"], L["due"], L["for_you"], L["chat"], L["from"], L["date"],
            L["notes"], L["link"]]
    out = []
    for t in rows:
        notes = "; ".join(f"{_lt(n.get('at'))}: {n.get('text')}" if isinstance(n, dict) else str(n)
                          for n in t.get("notes") or [])
        link = _row_link(store, t.get("folder"), t.get("msg_id"), L, t.get("link"), t.get("link_kind"), t.get("chat"))
        out.append([t.get("id"), t.get("text") or "", _task_status(t, L), t.get("due") or "",
                    L["yes"] if t.get("for_me") else "", t.get("chat") or "", t.get("sender") or "",
                    _lt(t.get("date")), notes, link])
    return head, out


def _item_line(it: dict, L: dict) -> str:
    who = it.get("sender") or ""
    when = _lt(it.get("date")) or it.get("time") or ""
    line = ", ".join(x for x in (when, who) if x)
    line = f"{line}: {it.get('text') or ''}" if line else (it.get("text") or "")
    r = it.get("in_reply_to")
    if isinstance(r, dict):
        quoted = f"“{r.get('text')}”" if r.get("text") is not None else L["not_local"]
        line += f" ({L['replying']} {r.get('sender') or ''}: {quoted})".replace(" : ", " ")
    return line


def _summary_blocks(doc: dict, chats: list[dict], quiet: list[str], L: dict,
                    store: Store | None = None) -> list[list]:
    """Blocks for each chat of a summary; returns the rows of its table too."""
    table = []
    for c in chats:
        def cell(item, _folder=c.get("folder"), _name=c.get("title")):
            return _row_link(store, _folder, item.get("id"), L, item.get("link"), item.get("link_kind"), _name)
        doc["blocks"].append({"h": 2, "text": c.get("title") or ""})
        span = " - ".join(x for x in (_lt(c.get("first")) or c.get("first") or "",
                                      _lt(c.get("last")) or c.get("last") or "") if x)
        meta = f"{c.get('messages') or 0} {L['messages']}" + (f", {span}" if span else "")
        if c.get("stale"):
            meta += f" ({L['stale']})"
        doc["blocks"].append({"meta": meta})
        people = c.get("people") or []
        if people:
            doc["blocks"].append({"p": f"{L['people']}: " + ", ".join(
                f"{p.get('name')} ({p.get('messages')})" for p in people if isinstance(p, dict))})
        threads = [t for t in c.get("threads") or [] if isinstance(t, dict)]
        if threads:
            doc["blocks"].append({"h": 3, "text": L["threads"]})
            doc["blocks"].append({"bullets": [
                {"text": f"{_item_line(t.get('root') or {}, L)} ({t.get('messages')} {L['messages']})",
                 "link": cell(t.get("root") or {})} for t in threads]})
        for key, label in (("questions", "questions"), ("decisions", "decisions"), ("tasks", "requests")):
            items = c.get(key) or []
            if items:
                doc["blocks"].append({"h": 3, "text": L[label]})
                doc["blocks"].append({"bullets": [{"text": _item_line(i, L), "link": cell(i)} for i in items]})
                table += [[c.get("title") or "", L[label], _lt(i.get("date")), i.get("sender") or "",
                           i.get("text") or "", cell(i)] for i in items]
        links = c.get("links") or []
        if links:
            doc["blocks"].append({"h": 3, "text": L["links"]})
            doc["blocks"].append({"bullets": [{"text": f"{_lt(x.get('date'))}, {x.get('sender') or ''}: {x.get('url')}",
                                               "link": cell(x)} for x in links]})
            table += [[c.get("title") or "", L["links"], _lt(x.get("date")), x.get("sender") or "",
                       x.get("url") or "", cell(x)] for x in links]
        files = c.get("files") or []
        if files:
            doc["blocks"].append({"h": 3, "text": L["files"]})
            doc["blocks"].append({"bullets": [{"text": f"{_lt(x.get('date'))}, {x.get('sender') or ''}: "
                                                       f"{x.get('name') or ''}", "link": cell(x)} for x in files]})
            table += [[c.get("title") or "", L["files"], _lt(x.get("date")), x.get("sender") or "",
                       x.get("name") or "", cell(x)] for x in files]
    if quiet:
        doc["blocks"].append({"meta": f"{L['quiet']}: {', '.join(q for q in quiet if q)}"})
    return table


def _summary_head(L: dict):
    return [L["chat"], L["kind"], L["date"], L["from"], L["text"], L["link"]]


def search_doc(store: Store, query: str, chat=None, since=None, lang="en") -> dict:
    if not (query or "").strip():
        raise UserError("What should be searched?", 'For example: tgkit export search "biên bản"')
    L = _labels(lang)
    data = engine.search(store, query, chat, since, limit=0)
    meta = [f"{data['total']} {L['messages']}" + (f", {L['words']}" if data["match"] == "words" else "")]
    if chat:
        meta.append(f"{L['in_chat']}: {chat}")
    if since:
        meta.append(f"{L['period']}: {since}")
    doc = _doc(f"{L['search']}: “{query}”", L, meta)
    doc["table"] = _hits_table(data["results"], L, store)
    doc["blocks"].append({"table": doc["table"]})
    return doc


def summary_doc(store: Store, chat=None, since=None, lang="en") -> dict:
    L = _labels(lang)
    dirs = store.match(chat)
    dg = digest.build(store, dirs, since)
    label = (dg.get("window") or {}).get("label") or since or ""
    chats = dg.get("chats") or []
    doc = _doc(f"{L['summary']}: {label}" if label else L["summary"], L,
               [f"{dg.get('total_messages', 0)} {L['messages']}, {len(chats)} {L['chats'].lower()}"]
               + ([f"{L['in_chat']}: {chat}"] if chat else []))
    quiet = [q.get("title") for q in dg.get("quiet_chats") or []]
    doc["table"] = (_summary_head(L), _summary_blocks(doc, chats, quiet, L, store))
    return doc


def tasks_doc(base: Path, store: Store, which="open", chat=None, since=None, lang="en") -> dict:
    """The task list as it is saved. Reads tasks.json only; it never scans or saves."""
    L = _labels(lang)
    which = which if which in ("open", "mine", "all") else "open"
    rows = engine.select_tasks(taskmod.TaskList(base), store, which, chat, since)
    meta = [f"{len(rows)}"] + ([f"{L['in_chat']}: {chat}"] if chat else []) + \
        ([f"{L['period']}: {since}"] if since else [])
    doc = _doc(L[f"which_{which}"], L, meta)
    doc["table"] = _tasks_table(rows, L, store)
    doc["blocks"].append({"table": doc["table"]})
    return doc


def card_doc(card: dict, lang="en", store: Store | None = None) -> dict:
    """A result card exactly as the app shows it (used when it cannot be run again)."""
    L = _labels(lang)
    kind = card.get("type") if isinstance(card, dict) else None
    items = [i for i in (card.get("items") or []) if isinstance(i, dict)] if kind else []
    if kind == "hits":
        doc = _doc(f"{L['search']}: “{card.get('query') or ''}”", L, [f"{card.get('total', len(items))} {L['messages']}"])
        doc["table"] = _hits_table(items, L, store)
        doc["blocks"].append({"table": doc["table"]})
        return doc
    if kind == "tasks":
        doc = _doc(L[f"which_{card.get('which')}"] if card.get("which") in ("open", "mine", "all") else L["tasks"], L)
        doc["table"] = _tasks_table(items, L, store)
        doc["blocks"].append({"table": doc["table"]})
        return doc
    if kind == "summary":
        chats = [c for c in card.get("chats") or [] if isinstance(c, dict)]
        doc = _doc(f"{L['summary']}: {card.get('label') or ''}".strip(": "), L,
                   [f"{card.get('total', 0)} {L['messages']}, {len(chats)} {L['chats'].lower()}"])
        doc["table"] = (_summary_head(L), _summary_blocks(doc, chats, card.get("quiet") or [], L, store))
        return doc
    if kind == "chats":
        doc = _doc(L["chats"], L)
        doc["table"] = ([L["chat"], L["count"], L["updated_col"]],
                        [[c.get("title") or "", c.get("messages") or 0, _lt(c.get("synced_at"))] for c in items])
        doc["blocks"].append({"table": doc["table"]})
        return doc
    raise UserError("This result cannot be saved as a file.", "Save the whole conversation instead.")


def _card_blocks(doc: dict, card: dict, lang: str, store: Store | None = None) -> None:
    try:
        sub = card_doc(card, lang, store)
    except UserError:
        return
    doc["blocks"].append({"h": 3, "text": sub["title"]})
    doc["blocks"].extend(sub["blocks"])


def conversation_doc(base: Path, ident: str, lang="en") -> dict:
    from .conversations import Conversations  # noqa: PLC0415

    L = _labels(lang)
    conv = Conversations(base).get(ident)
    doc = _doc(conv.get("title") or L["conversation"], L, [f"{L['conversation']}, {_lt(conv.get('created'))}"])
    rows = []
    for turn in conv.get("turns") or []:
        if turn.get("role") == "user":
            who = L["you"]
        else:
            who = "TG Kit (" + ("Claude" if turn.get("mode") == "claude" else "offline") + ")"
            if turn.get("model"):
                who += f", {turn['model']}"
        when = _lt(turn.get("at"))
        doc["blocks"].append({"h": 2, "text": f"{who}, {when}" if when else who})
        doc["blocks"].append({"p": turn.get("text") or ""})
        rows.append([when, who, turn.get("text") or ""])
        for card in turn.get("cards") or []:
            if isinstance(card, dict):
                _card_blocks(doc, card, lang, Store(base))
    doc["table"] = ([L["time"], L["who"], L["text"]], rows)
    return doc


def build(base: Path, kind: str, *, query=None, chat=None, since=None, which="open",
          conversation=None, card=None, lang="en") -> dict:
    """The document for one export. A card that says how it was made is run again,
    so the file holds every result, not only the ones the card had room for."""
    store = Store(base)
    if kind == "card":
        src = card.get("source") if isinstance(card, dict) else None
        if isinstance(src, dict) and src.get("kind") in ("search", "summary", "tasks"):
            kind, query, chat, since = src["kind"], src.get("query"), src.get("chat"), src.get("since")
            which = src.get("which") or which
        else:
            return card_doc(card or {}, lang, store)
    if kind == "search":
        return search_doc(store, query or "", chat, since, lang)
    if kind == "summary":
        return summary_doc(store, chat, since, lang)
    if kind == "tasks":
        return tasks_doc(base, store, which, chat, since, lang)
    if kind == "conversation":
        return conversation_doc(base, conversation or "", lang)
    raise UserError(f"TG Kit cannot export '{kind}'.", "Export a conversation, a search, a summary or the tasks.")


# ------------------------------------------------------------------ writers

_BAD_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")


def _x(text) -> str:
    return escape(_BAD_XML.sub("", "" if text is None else str(text)))


def _text(value) -> str:
    if isinstance(value, dict):
        return str(value.get("text") or value.get("url") or "")
    return "" if value is None else str(value)


def _run(text, bold=False) -> str:
    parts = _text(text).replace("\r\n", "\n").split("\n")
    props = "<w:rPr><w:b/></w:rPr>" if bold else ""
    inner = "<w:br/>".join(f'<w:t xml:space="preserve">{_x(p)}</w:t>' for p in parts)
    return f"<w:r>{props}{inner}</w:r>"


def _hyperlink(cell: dict, links: list) -> str:
    rid = f"rIdL{len(links) + 1}"
    links.append((rid, str(cell["url"])))
    return (f'<w:hyperlink r:id="{rid}" w:history="1"><w:r><w:rPr><w:color w:val="0B5CAD"/>'
            f'<w:u w:val="single"/></w:rPr><w:t xml:space="preserve">{_x(_text(cell))}</w:t></w:r>'
            f"</w:hyperlink>")


def _para(text, style=None, bold=False, links: list | None = None) -> str:
    ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    if isinstance(text, dict) and text.get("url") and links is not None:
        return f"<w:p>{ppr}{_hyperlink(text, links)}</w:p>"
    return f"<w:p>{ppr}{_run(text, bold)}</w:p>"


def _bullet(item, links: list) -> str:
    """A list line; a summary line ends with its link to Telegram."""
    if isinstance(item, dict):
        link = item.get("link")
        tail = (_run(" (") + _hyperlink(link, links) + _run(")")) if isinstance(link, dict) and link.get("url") else (
            _run(f" ({_text(link)})") if isinstance(link, dict) and _text(link) else "")
        line = "• " + str(item.get("text") or "")
        return f'<w:p><w:pPr><w:pStyle w:val="ListBullet"/></w:pPr>{_run(line)}{tail}</w:p>'
    return _para(f"• {item}", "ListBullet")


def _docx_table(head, rows, links: list | None = None) -> str:
    border = "".join(f'<w:{s} w:val="single" w:sz="4" w:space="0" w:color="C3C8D0"/>'
                     for s in ("top", "left", "bottom", "right", "insideH", "insideV"))
    out = [f'<w:tbl><w:tblPr><w:tblStyle w:val="TGTable"/><w:tblW w:w="5000" w:type="pct"/>'
           f'<w:tblBorders>{border}</w:tblBorders><w:tblLayout w:type="autofit"/>'
           f'<w:tblCellMar><w:top w:w="40" w:type="dxa"/><w:left w:w="80" w:type="dxa"/>'
           f'<w:bottom w:w="40" w:type="dxa"/><w:right w:w="80" w:type="dxa"/></w:tblCellMar></w:tblPr>']

    def row(cells, header=False):
        trpr = "<w:trPr><w:tblHeader/><w:cantSplit/></w:trPr>" if header else "<w:trPr><w:cantSplit/></w:trPr>"
        shade = '<w:shd w:val="clear" w:color="auto" w:fill="E9ECF1"/>' if header else ""
        tcs = "".join(f"<w:tc><w:tcPr>{shade}</w:tcPr>{_para(c, 'TableText', header, links)}</w:tc>"
                      for c in cells)
        return f"<w:tr>{trpr}{tcs}</w:tr>"
    out.append(row(head, True))
    out += [row(r) for r in rows]
    out.append("</w:tbl>")
    return "".join(out)


_STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:cs="Calibri" w:eastAsia="Calibri"/>
<w:sz w:val="22"/><w:szCs w:val="22"/><w:lang w:val="vi-VN"/></w:rPr></w:rPrDefault>
<w:pPrDefault><w:pPr><w:spacing w:after="120" w:line="276" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/></w:style>
<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/>
<w:pPr><w:spacing w:after="80"/></w:pPr><w:rPr><w:b/><w:color w:val="1B1D21"/><w:sz w:val="40"/><w:szCs w:val="40"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Meta"><w:name w:val="Meta"/><w:basedOn w:val="Normal"/>
<w:pPr><w:spacing w:after="40"/></w:pPr><w:rPr><w:color w:val="555B66"/><w:sz w:val="18"/><w:szCs w:val="18"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/>
<w:pPr><w:keepNext/><w:spacing w:before="320" w:after="120"/><w:outlineLvl w:val="0"/></w:pPr><w:rPr><w:b/><w:sz w:val="30"/><w:szCs w:val="30"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/>
<w:pPr><w:keepNext/><w:spacing w:before="280" w:after="80"/><w:outlineLvl w:val="1"/></w:pPr><w:rPr><w:b/><w:color w:val="0B5CAD"/><w:sz w:val="26"/><w:szCs w:val="26"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading3"><w:name w:val="heading 3"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/>
<w:pPr><w:keepNext/><w:spacing w:before="200" w:after="60"/><w:outlineLvl w:val="2"/></w:pPr><w:rPr><w:b/><w:sz w:val="22"/><w:szCs w:val="22"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="ListBullet"><w:name w:val="List Bullet"/><w:basedOn w:val="Normal"/>
<w:pPr><w:spacing w:after="60"/><w:ind w:left="360" w:hanging="240"/></w:pPr></w:style>
<w:style w:type="paragraph" w:styleId="TableText"><w:name w:val="Table Text"/><w:basedOn w:val="Normal"/>
<w:pPr><w:spacing w:after="0" w:line="252" w:lineRule="auto"/></w:pPr><w:rPr><w:sz w:val="19"/><w:szCs w:val="19"/></w:rPr></w:style>
<w:style w:type="table" w:styleId="TGTable"><w:name w:val="TG Kit Table"/><w:tblPr><w:tblInd w:w="0" w:type="dxa"/></w:tblPr></w:style>
</w:styles>"""


def to_docx(doc: dict) -> bytes:
    links: list = []
    body = [_para(doc["title"], "Title")] + [_para(m, "Meta") for m in doc.get("meta") or []]
    for b in doc.get("blocks") or []:
        if "h" in b:
            body.append(_para(b["text"], f"Heading{min(3, max(1, int(b['h'])))}"))
        elif "p" in b:
            body.append(_para(b["p"]))
        elif "meta" in b:
            body.append(_para(b["meta"], "Meta"))
        elif "bullets" in b:
            body += [_bullet(t, links) for t in b["bullets"]]
        elif "table" in b:
            head, rows = b["table"]
            body.append(_docx_table(head, rows, links) if rows else _para("-", "Meta"))
            body.append(_para(""))
    if not doc.get("blocks"):
        body.append(_para("-", "Meta"))
    landscape = any("table" in b and len(b["table"][0]) > 6 for b in doc.get("blocks") or [])
    link_rels = "".join(
        f'<Relationship Id="{rid}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" '
        f'Target="{_x(url)}" TargetMode="External"/>' for rid, url in links)
    size = '<w:pgSz w:w="16838" w:h="11906" w:orient="landscape"/>' if landscape else '<w:pgSz w:w="11906" w:h="16838"/>'
    document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><w:body>'
                + "".join(body)
                + f'<w:sectPr>{size}<w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134" '
                  'w:header="567" w:footer="567" w:gutter="0"/></w:sectPr></w:body></w:document>')
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    files = {
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            '<Override PartName="/word/styles.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
            '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
            '</Types>'),
        "_rels/.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="word/document.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" '
            'Target="docProps/core.xml"/></Relationships>'),
        "word/_rels/document.xml.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
            f'Target="styles.xml"/>{link_rels}</Relationships>'),
        "word/document.xml": document,
        "word/styles.xml": _STYLES,
        "docProps/core.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            f'<dc:title>{_x(doc["title"])}</dc:title><dc:creator>TG Kit</dc:creator>'
            f'<dcterms:created xsi:type="dcterms:W3CDTF">{now}</dcterms:created></cp:coreProperties>'),
    }
    return _zip(files)


def _zip(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in files.items():
            z.writestr(name, text.encode("utf-8"))
    return buf.getvalue()


def _col(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def _cell(ref: str, value, style: int) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'<c r="{ref}" s="{style}"><v>{value}</v></c>'
    if isinstance(value, dict) and value.get("url"):
        url, text = str(value["url"]).replace('"', '""'), _text(value).replace('"', '""')
        formula = 'HYPERLINK("' + url + '","' + text + '")'
        return f'<c r="{ref}" s="3" t="str"><f>{_x(formula)}</f><v>{_x(_text(value))}</v></c>'
    text = _x(_text(value) if isinstance(value, dict) else value)[:32000]
    return f'<c r="{ref}" s="{style}" t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>'


def to_xlsx(doc: dict) -> bytes:
    head, rows = doc.get("table") or ([doc.get("title") or ""], [])
    widths = [min(60, max(8, len(str(h)) + 2, *(min(60, len(_text(r[i] if i < len(r) else ""))) + 2
                                                 for r in rows[:500]))) for i, h in enumerate(head)]
    cols = "".join(f'<col min="{i + 1}" max="{i + 1}" width="{w}" customWidth="1"/>' for i, w in enumerate(widths))
    sheet_rows = [f'<row r="1">' + "".join(_cell(f"{_col(i)}1", h, 1) for i, h in enumerate(head)) + "</row>"]
    for n, r in enumerate(rows, 2):
        sheet_rows.append(f'<row r="{n}">' + "".join(_cell(f"{_col(i)}{n}", v, 2) for i, v in enumerate(r)) + "</row>")
    last = f"{_col(max(0, len(head) - 1))}{max(1, len(rows) + 1)}"
    sheet = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
             '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" '
             'state="frozen"/></sheetView></sheetViews>'
             f'<cols>{cols}</cols><sheetData>{"".join(sheet_rows)}</sheetData>'
             f'<autoFilter ref="A1:{last}"/></worksheet>')
    name = re.sub(r"[\[\]:*?/\\]", " ", doc.get("title") or "TG Kit").strip()[:31] or "TG Kit"
    files = {
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '<Override PartName="/xl/styles.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>'),
        "_rels/.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="xl/workbook.xml"/></Relationships>'),
        "xl/workbook.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f'<sheets><sheet name="{_x(name)}" sheetId="1" r:id="rId1"/></sheets>'
            '<definedNames><definedName name="_xlnm._FilterDatabase" localSheetId="0" hidden="1">'
            f"&apos;{_x(name)}&apos;!$A$1:${_col(max(0, len(head) - 1))}${max(1, len(rows) + 1)}"
            '</definedName></definedNames></workbook>'),
        "xl/_rels/workbook.xml.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            'Target="worksheets/sheet1.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
            'Target="styles.xml"/></Relationships>'),
        "xl/worksheets/sheet1.xml": sheet,
        "xl/styles.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<fonts count="3"><font><sz val="11"/><name val="Calibri"/></font>'
            '<font><b/><sz val="11"/><name val="Calibri"/></font>'
            '<font><u/><sz val="11"/><color rgb="FF0B5CAD"/><name val="Calibri"/></font></fonts>'
            '<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
            '<fill><patternFill patternType="solid"><fgColor rgb="FFE9ECF1"/><bgColor indexed="64"/></patternFill></fill></fills>'
            '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
            '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
            '<cellXfs count="4"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
            '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/>'
            '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1">'
            '<alignment vertical="top" wrapText="1"/></xf>'
            '<xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" applyFont="1" applyAlignment="1">'
            '<alignment vertical="top"/></xf></cellXfs>'
            '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>'),
    }
    return _zip(files)


def to_csv(doc: dict) -> bytes:
    """UTF-8 with a byte order mark, so Excel shows Vietnamese accents correctly."""
    head, rows = doc.get("table") or ([doc.get("title") or ""], [])
    buf = io.StringIO()
    w = csv.writer(buf)
    # each link column holds the bare URL; the link_note column after it says what it opens
    links = {i for i, h in enumerate(head) if h in _LINK_HEADS}
    w.writerow([x for i, h in enumerate(head) for x in ((h, "link_note") if i in links else (h,))])
    for r in rows:
        out = []
        for i, v in enumerate(r):
            out.append((v.get("url") or "") if isinstance(v, dict) else ("" if v is None else v))
            if i in links:
                out.append(v.get("note") or "" if isinstance(v, dict) else "")
        w.writerow(out)
    return ("\ufeff" + buf.getvalue()).encode("utf-8")


def _md_cell(v) -> str:
    if isinstance(v, dict):
        return f"[{_md_cell(_text(v))}]({v.get('url')})" if v.get("url") else _md_cell(_text(v))
    return ("" if v is None else str(v)).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _md_bullet(item) -> str:
    if isinstance(item, dict):
        link = item.get("link")
        tail = f" ({_md_cell(link)})" if isinstance(link, dict) and _text(link) else ""
        return _md_cell(item.get("text") or "") + tail
    return _md_cell(item)


def to_md(doc: dict) -> bytes:
    out = [f"# {doc['title']}", ""] + [f"_{m}_  " for m in doc.get("meta") or []] + [""]
    for b in doc.get("blocks") or []:
        if "h" in b:
            out += ["#" * (min(3, int(b["h"])) + 1) + f" {b['text']}", ""]
        elif "p" in b:
            out += [b["p"], ""]
        elif "meta" in b:
            out += [f"_{b['meta']}_", ""]
        elif "bullets" in b:
            out += [f"- {_md_bullet(t)}" for t in b["bullets"]] + [""]
        elif "table" in b:
            head, rows = b["table"]
            out.append("| " + " | ".join(_md_cell(h) for h in head) + " |")
            out.append("|" + "---|" * len(head))
            out += ["| " + " | ".join(_md_cell(v) for v in r) + " |" for r in rows]
            out.append("")
    return "\n".join(out).encode("utf-8")


WRITERS = {"docx": to_docx, "xlsx": to_xlsx, "csv": to_csv, "md": to_md}


# ------------------------------------------------------------------ where files go


def documents() -> Path:
    """The user's Documents folder (on Windows also when OneDrive moved it)."""
    if os.name == "nt":
        try:
            import ctypes  # noqa: PLC0415
            from ctypes import wintypes  # noqa: PLC0415
            from uuid import UUID  # noqa: PLC0415

            class GUID(ctypes.Structure):
                _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                            ("Data3", wintypes.WORD), ("Data4", wintypes.BYTE * 8)]
            u = UUID("FDD39AD0-238F-46AF-ADB4-6C85480369C7")  # FOLDERID_Documents
            guid = GUID(u.time_low, u.time_mid, u.time_hi_version, (wintypes.BYTE * 8)(*u.bytes[8:]))
            out = ctypes.c_wchar_p()
            if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(out)) == 0:
                found = Path(out.value)
                ctypes.windll.ole32.CoTaskMemFree(out)
                return found
        except (OSError, AttributeError, ValueError):
            pass
    docs = Path.home() / "Documents"
    return docs if docs.exists() else Path.home()


def default_dir() -> Path:
    return documents() / "TG Kit"


def export_dir(base: Path) -> Path:
    env = os.environ.get("TGKIT_EXPORT_DIR")
    if env:
        return Path(env).expanduser()
    chosen = (config.load(base) if config.config_path(base).exists() else {}).get("export_dir")
    return Path(chosen).expanduser() if chosen else default_dir()


_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def safe_name(text: str, limit: int = 80) -> str:
    """A file name that works on Windows, macOS and Linux, keeping Vietnamese letters."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", text or "")
    name = re.sub(r"\s+", " ", name).strip().strip(".")
    name = name[:limit].rstrip(" .")
    if not name or name.split(".")[0].upper() in _RESERVED:
        name = f"TG Kit {name}".strip()
    return name


def _write_new(folder: Path, stem: str, ext: str, data: bytes) -> Path:
    """Create a NEW file: never overwrite one, add " (2)", " (3)" ... instead."""
    folder.mkdir(parents=True, exist_ok=True)
    for n in range(1, 1000):
        p = folder / (f"{stem}.{ext}" if n == 1 else f"{stem} ({n}).{ext}")
        try:
            fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o644)
        except FileExistsError:
            continue
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        return p
    raise UserError(f"Too many files with this name in {folder}.", "Move some of them away and try again.")


def _folder(base: Path, folder) -> Path:
    target = Path(folder).expanduser() if folder else export_dir(base)
    if not target.is_absolute():
        raise UserError(f"'{folder}' is not a full folder path.", r"Use a full path, like C:\Users\you\Documents\TG Kit")
    return target


def save(base: Path, doc: dict, fmt: str = "docx", folder=None) -> Path:
    fmt = (fmt or "docx").lower().lstrip(".")
    if fmt not in WRITERS:
        raise UserError(f"TG Kit cannot save .{fmt} files.", "Choose docx (Word), xlsx (Excel), csv or md.")
    stem = safe_name(f"{doc['title']} {datetime.now().strftime('%Y-%m-%d')}")
    try:
        return _write_new(_folder(base, folder), stem, fmt, WRITERS[fmt](doc))
    except OSError as exc:
        raise UserError(f"The file could not be saved ({exc.strerror or exc}).",
                        "Check the export folder in Settings, or choose another one.") from None


def save_attachment(base: Path, folder: str, msg_id, dest=None) -> Path:
    """Copy a file that is already in the chat's media folder. Never downloads."""
    store = Store(base)
    names = {d.name for d in store.chat_dirs()}
    if folder not in names:
        raise UserError("That chat is not in TG Kit.", "Pick the file from the results again.")
    try:
        ident = int(msg_id)
    except (TypeError, ValueError):
        raise UserError("That message was not found.", "Pick the file from the results again.") from None
    row = next((r for r in store.rows(store.chats / folder) if r.get("id") == ident), None)
    if row is None:
        raise UserError("That message was not found in the local copy.", "Pick the file from the results again.")
    rel = row.get("media_path")
    root = (store.chats / folder / "media").resolve()
    src = (store.chats / rel).resolve() if rel else None
    if not src or not src.is_relative_to(root) or not src.is_file():
        name = (row.get("media") or {}).get("file_name") or "this file"
        raise UserError(f"{name} is not on this computer.",
                        "TG Kit only copies files it got during sync (big files are skipped). "
                        "Open the message in Telegram to download it.")
    target = _folder(base, dest)
    target.mkdir(parents=True, exist_ok=True)
    try:
        return _write_new(target, safe_name(src.stem, 120), src.suffix.lstrip(".") or "bin", src.read_bytes())
    except OSError as exc:
        raise UserError(f"The file could not be copied ({exc.strerror or exc}).",
                        "Check the export folder in Settings, or choose another one.") from None


def reveal(path: Path) -> bool:
    """Show the saved file in Explorer / Finder. TGKIT_NO_REVEAL=1 turns it off (tests)."""
    if os.environ.get("TGKIT_NO_REVEAL"):
        return False
    path = Path(path)
    try:
        if os.name == "nt":
            if path.is_dir():
                os.startfile(str(path))  # noqa: S606
            else:
                subprocess.Popen(["explorer", f"/select,{path}"])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(path)] if path.is_file() else ["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path if path.is_dir() else path.parent)])
        return True
    except OSError:
        return False


def open_folder(base: Path) -> Path:
    folder = export_dir(base)
    folder.mkdir(parents=True, exist_ok=True)
    reveal(folder)
    return folder
