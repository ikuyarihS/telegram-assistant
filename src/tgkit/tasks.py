"""Your task list: task-like messages found in your chats, plus tasks you add.

Kept in <data>/tasks.json. Finding tasks only READS messages.jsonl; it never
writes to a chat folder. A task you mark done or drop stays in the file, so a
later scan never brings it back. Every write goes through TaskList.change()
(tasks.lock, read again, change, atomic write), so a Done click and a scan
running at the same time both keep their change.
"""

import re
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .errors import UserError
from .store import Store, TasksLock, fold, local_time, parse_date, parse_since, read_json, write_json

DEFAULT_WINDOW = "30d"

_EN = re.compile(
    r"\b(todo|to-do|action items?|please|pls|plz|can you|could you|would you|"
    r"need(?:s)? to|have to|must|deadline|due|remind(?:er)?|follow[- ]up|asap)\b",
    re.IGNORECASE,
)
_VI = re.compile(
    r"(?<!\w)(việc cần làm|cần làm|nhờ|nhớ|giúp|hạn chót|xử lý|cần|phải|nhiệm vụ|giao việc)(?!\w)",
    re.IGNORECASE,
)
# The same cues typed without accents.
_VI_PLAIN = re.compile(
    r"\b(viec can lam|can lam|lam giup|gui giup|check giup|xu ly giup|nho (?:em|anh|chi|ban))\b"
)
_MENTION = re.compile(r"@([A-Za-z0-9_]{3,})")
_DUE = [
    re.compile(r"\b\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?\b"),
    re.compile(r"\b\d{1,2}h(?:\d{2})?\b|\b\d{1,2}:\d{2}\b", re.IGNORECASE),
    re.compile(
        r"\b(?:by |before |until )?(?:today|tonight|tomorrow|eod|end of (?:the )?(?:day|week)|"
        r"this week|next week|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?<!\w)(?:trước |hạn |trong )?(?:hôm nay|ngày mai|sáng mai|chiều mai|tối mai|"
        r"tuần này|tuần sau|cuối tuần|cuối ngày|thứ [2-7]|thứ (?:hai|ba|tư|năm|sáu|bảy)|chủ nhật)(?!\w)",
        re.IGNORECASE,
    ),
]


def detect(text: str) -> dict | None:
    """Task cues in one message, or None. Returns {"due": str|None, "assignees": [...]}."""
    text = unicodedata.normalize("NFC", text or "")
    if len(text.strip()) < 6:
        return None
    if not (_EN.search(text) or _VI.search(text) or _VI_PLAIN.search(fold(text))):
        return None
    spans = []
    for rx in _DUE:
        for m in rx.finditer(text):
            if not any(a <= m.start() < b or m.start() <= a < m.end() for a, b, _ in spans):
                spans.append((m.start(), m.end(), m.group(0).strip()))
    spans.sort()
    due = ", ".join(s for _, _, s in spans) or None
    return {"due": due, "assignees": _MENTION.findall(text)}


class TaskList:
    def __init__(self, home: Path):
        self.home = Path(home)
        self.path = self.home / "tasks.json"
        self.data = self._load()

    def _load(self) -> dict:
        data = read_json(self.path, None)
        if data is None:
            data = {"version": 1, "next_id": 1, "tasks": []}
        if not isinstance(data, dict) or not isinstance(data.get("tasks"), list):
            raise UserError(
                f"The task list {self.path} is not in the expected shape. Nothing was changed.",
                "Rename or fix tasks.json and try again.",
            )
        return data

    @property
    def tasks(self) -> list[dict]:
        return self.data["tasks"]

    def save(self) -> None:
        write_json(self.path, self.data)

    @contextmanager
    def change(self):
        """Every change of tasks.json: take the tasks lock, read the file again (another
        Telegram Assistant may have changed it since this list was read), apply the change, write
        it atomically, free the lock. If the change raises, nothing is written."""
        with TasksLock(self.home):
            self.data = self._load()
            yield self
            self.save()

    def _new_id(self) -> int:
        n = int(self.data.get("next_id") or 1)
        used = {t.get("id") for t in self.tasks}
        while n in used:
            n += 1
        self.data["next_id"] = n + 1
        return n

    def get(self, ident: str) -> dict:
        try:
            n = int(str(ident).lstrip("#"))
        except ValueError:
            raise UserError(f"'{ident}' is not a task number.",
                            "Use the number shown in: tgkit tasks") from None
        for t in self.tasks:
            if t.get("id") == n:
                return t
        raise UserError(f"There is no task number {n}.", "See the numbers with: tgkit tasks all")

    # finding -------------------------------------------------------------

    def scan(self, store: Store, dirs: list[Path], since: datetime | None, me: dict | None) -> int:
        """Find and add new tasks in memory (save with change()). Returns how many."""
        return self.merge(self.find(store, dirs, since, me))

    def merge(self, found: list[dict]) -> int:
        """Add the found tasks this list does not hold yet, with new numbers."""
        known = {t.get("key") for t in self.tasks}
        added = 0
        for task in found:
            if task["key"] in known:
                continue
            self.tasks.append({"id": self._new_id(), **task})
            known.add(task["key"])
            added += 1
        return added

    def find(self, store: Store, dirs: list[Path], since: datetime | None, me: dict | None) -> list[dict]:
        """Task-like messages not in this list yet, without numbers. Only reads."""
        known = {t.get("key") for t in self.tasks}
        me = me or {}
        my_id = me.get("id")
        my_user = (me.get("username") or "").lower()
        out = []
        for d in dirs:
            rs = store.rows(d)
            by_id = {r.get("id"): r for r in rs}
            dm = store.meta(d).get("type") == "User"
            for r in rs:
                key = f"{d.name}#{r.get('id')}"
                if key in known:
                    continue
                when = parse_date(r.get("date"))
                if since and (when is None or when < since):
                    continue
                cue = detect(r.get("text") or "")
                if not cue:
                    continue
                parent = by_id.get(r.get("reply_to_msg_id")) or {}
                for_me = bool(
                    (my_user and my_user in [a.lower() for a in cue["assignees"]])
                    or (dm and my_id is not None and r.get("sender_id") != my_id)
                    or (my_id is not None and parent.get("sender_id") == my_id
                        and r.get("sender_id") != my_id)
                )
                out.append({
                    "key": key,
                    "source": "message",
                    "chat": store.title(d),
                    "folder": d.name,
                    "msg_id": r.get("id"),
                    "date": r.get("date"),
                    "sender": store.sender_name(r.get("sender_id")),
                    "text": r.get("text") or "",
                    "due": cue["due"],
                    "assignees": cue["assignees"],
                    "for_me": for_me,
                    "status": "open",
                    "notes": [],
                })
                known.add(key)
        return out

    # changing ------------------------------------------------------------

    def add(self, text: str, due: str | None = None) -> dict:
        task = {
            "id": self._new_id(),
            "key": None,
            "source": "manual",
            "chat": None,
            "folder": None,
            "msg_id": None,
            "date": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "sender": None,
            "text": text,
            "due": due,
            "assignees": [],
            "for_me": True,
            "status": "open",
            "notes": [],
        }
        self.tasks.append(task)
        return task

    def set_status(self, ident: str, status: str) -> dict:
        t = self.get(ident)
        t["status"] = status
        t["done_at"] = (datetime.now(timezone.utc).replace(microsecond=0).isoformat()
                        if status == "done" else None)
        return t

    def note(self, ident: str, text: str) -> dict:
        t = self.get(ident)
        t.setdefault("notes", []).append({
            "at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "text": text,
        })
        return t

    # showing -------------------------------------------------------------

    def select(self, which: str) -> list[dict]:
        if which == "all":
            rows = list(self.tasks)
        elif which == "mine":
            rows = [t for t in self.tasks if t.get("status") == "open" and t.get("for_me")]
        else:
            rows = [t for t in self.tasks if t.get("status") == "open"]
        return sorted(rows, key=lambda t: (not t.get("for_me"), _neg_time(t.get("date"))))


def _neg_time(value) -> float:
    dt = parse_date(value)
    return -dt.timestamp() if dt else 0.0


def render(task: dict) -> str:
    who = []
    if task.get("for_me"):
        who.append("for you")
    if task.get("status") != "open":
        who.append(task.get("status"))
    tag = f"[{', '.join(who)}] " if who else ""
    text = (task.get("text") or "").replace("\n", " / ")
    if len(text) > 200:
        text = text[:200] + "..."
    where = ""
    if task.get("source") == "message":
        where = f"{task.get('sender')} in {task.get('chat')}, {local_time(task.get('date'))}: "
    line = f"{task['id']:>4}. {tag}{where}{text}"
    if task.get("due"):
        line += f"   (due: {task['due']})"
    for n in task.get("notes") or []:
        line += f"\n        note {local_time(n.get('at'))}: {n.get('text')}"
    return line


def window_start(since: str | None) -> datetime | None:
    return parse_since(since, DEFAULT_WINDOW)
