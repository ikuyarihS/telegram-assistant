"""What the user asked in the TG Kit app, kept as JSON in the data folder.

    <data>/app/conversations/<id>.json   one conversation: its turns, the mode
                                         that answered, the Claude session id

Claude Code (or anyone) can read them with `tgkit chats-log --json`. Written
only through store.write_json (atomic). A conversation the user removes is
marked archived, not deleted.
"""

import re
import secrets
from datetime import datetime, timezone
from pathlib import Path

from .errors import UserError
from .store import read_json, write_json

_ID = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{6}$")
TITLE_MAX = 60


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _title(text: str) -> str:
    line = " ".join((text or "").split())
    return line if len(line) <= TITLE_MAX else line[: TITLE_MAX - 3].rstrip() + "..."


class Conversations:
    def __init__(self, home: Path):
        self.dir = Path(home) / "app" / "conversations"

    def _path(self, ident: str) -> Path:
        if not isinstance(ident, str) or not _ID.match(ident):
            raise UserError("That conversation does not exist.", "Pick one from the list on the left.")
        return self.dir / f"{ident}.json"

    def new(self, first_text: str = "") -> dict:
        now = datetime.now(timezone.utc)
        ident = f"{now:%Y%m%d-%H%M%S}-{secrets.token_hex(3)}"
        return {"id": ident, "title": _title(first_text) or "New conversation", "created": _now(),
                "updated": _now(), "archived": False, "claude_session": None, "turns": []}

    def get(self, ident: str) -> dict:
        path = self._path(ident)
        conv = read_json(path, None)
        if not isinstance(conv, dict):
            raise UserError("That conversation does not exist.", "Pick one from the list on the left.")
        return conv

    def save(self, conv: dict) -> None:
        conv["updated"] = _now()
        write_json(self._path(conv["id"]), conv)

    def add_turn(self, conv: dict, role: str, **fields) -> dict:
        turn = {"role": role, "at": _now(), **fields}
        conv.setdefault("turns", []).append(turn)
        self.save(conv)
        return turn

    def archive(self, ident: str) -> None:
        conv = self.get(ident)
        conv["archived"] = True
        self.save(conv)

    def all(self, include_archived: bool = False) -> list[dict]:
        if not self.dir.exists():
            return []
        out = []
        for p in self.dir.glob("*.json"):
            if not _ID.match(p.stem):
                continue
            try:
                conv = read_json(p, None)
            except UserError:
                continue
            if isinstance(conv, dict) and (include_archived or not conv.get("archived")):
                out.append(conv)
        out.sort(key=lambda c: c.get("updated") or "", reverse=True)
        return out

    def index(self) -> list[dict]:
        return [{"id": c["id"], "title": c.get("title"), "updated": c.get("updated"),
                 "turns": len(c.get("turns") or [])} for c in self.all()]


def log_view(conv: dict, cards: bool = False) -> dict:
    """A conversation as `tgkit chats-log --json` prints it."""
    turns = []
    for t in conv.get("turns") or []:
        item = {"role": t.get("role"), "at": t.get("at"), "text": t.get("text")}
        if t.get("role") == "assistant":
            item["mode"] = t.get("mode")
            if t.get("intent"):
                item["intent"] = t["intent"]
            if t.get("steps"):
                item["commands"] = [s.get("command") for s in t["steps"]]
            if cards:
                item["cards"] = t.get("cards") or []
        turns.append(item)
    return {"id": conv.get("id"), "title": conv.get("title"), "created": conv.get("created"),
            "updated": conv.get("updated"), "turns": turns}
