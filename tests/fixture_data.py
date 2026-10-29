"""A synthetic TG Kit data folder: fake chats and fake people, no real data.

Dates are relative to now so "last 7 days" windows keep working. Every
messages.jsonl is written NEWEST first, like the real thing. One chat folder
and one file name are non-ASCII on purpose.
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PEOPLE = {"101": "An Nguyễn", "102": "Bình Trần", "103": "Chi Lê", "104": "Dũng Phạm"}
ME = {"id": 101, "name": "An Nguyễn", "username": "an_test"}
MINUTES_FILE = "Biên bản họp 28-10.pdf"


def _ago(hours: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).replace(microsecond=0).isoformat()


def _row(i, hours, sender, text, reply=None, media=None, media_path=None):
    return {
        "id": i, "date": _ago(hours), "text": text, "views": None, "forwards": None,
        "reply_to_msg_id": reply, "sender_id": sender, "grouped_id": None,
        "media": media, "media_path": media_path,
    }


def chats() -> list[tuple[str, dict, list[dict]]]:
    alpha = [
        _row(10, 24 * 40, 102, "Kickoff dự án Alpha, mọi người xem tài liệu nhé"),
        _row(20, 72, 102, "Mọi người ơi, mình họp lúc 3h chiều mai được không?"),
        _row(21, 71, 103, "Được ạ, em đồng ý", reply=20),
        _row(22, 48, 101, "Chốt phương án B cho phần thanh toán"),
        _row(23, 47, 102, "@an_test nhờ em gửi biên bản họp trước thứ 6 nhé"),
        _row(24, 24, 104, "Biên bản họp đây ạ", reply=23,
             media={"kind": "MessageMediaDocument", "mime_type": "application/pdf",
                    "size": 1234, "file_name": MINUTES_FILE},
             media_path=str(Path("Du_an_Alpha") / "media" / MINUTES_FILE)),
        _row(25, 23, 103, "Link thiết kế: https://example.com/design/alpha?v=2."),
        _row(26, 5, 102, "Please review the API spec by Friday @chi_test"),
    ]
    beta = [
        _row(50, 24 * 10, 103, "TODO: cập nhật tài liệu hướng dẫn"),
        _row(51, 48, 104, "Ai biết mật khẩu wifi phòng họp không?"),
        _row(52, 24, 101, "Hôm nay ăn trưa ở đâu?"),
        _row(53, 6, 103, "Cần làm xong báo cáo tuần trước 17h hôm nay"),
    ]
    ketoan = [
        _row(70, 48, 102, "Hóa đơn tháng 10 đã gửi", media={"kind": "photo"}),
        _row(71, 24, 104, "Ok cảm ơn", reply=70),
    ]
    return [
        ("Du_an_Alpha", {"id": 1001, "type": "Channel", "title": "Dự án Alpha: Kế hoạch",
                         "username": None, "megagroup": True}, alpha),
        ("Team_Beta", {"id": 2002, "type": "Chat", "title": "Team Beta", "username": None}, beta),
        ("Phòng_Kế_toán", {"id": 3003, "type": "Channel", "title": "Phòng Kế toán",
                           "username": "ketoan_test", "megagroup": True}, ketoan),
        ("Empty_Chat", {"id": 4004, "type": "User", "title": None, "username": None,
                        "first_name": "Em", "last_name": "Test"}, []),
    ]


def write_chat(home: Path, folder: str, meta: dict, rows: list[dict]) -> Path:
    d = home / "chats" / folder
    d.mkdir(parents=True, exist_ok=True)
    (d / "channel.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    newest_first = sorted(rows, key=lambda r: r["id"], reverse=True)
    (d / "messages.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in newest_first), encoding="utf-8")
    for r in rows:
        if r.get("media_path"):
            f = home / "chats" / r["media_path"]
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(b"%PDF-1.4 fake\n")
    return d


def build(home) -> Path:
    home = Path(home)
    home.mkdir(parents=True, exist_ok=True)
    for folder, meta, rows in chats():
        write_chat(home, folder, meta, rows)
    (home / "senders.json").write_text(json.dumps(PEOPLE, ensure_ascii=False, indent=2), encoding="utf-8")
    (home / "config.json").write_text(json.dumps({"me": ME}, ensure_ascii=False, indent=2), encoding="utf-8")
    return home


if __name__ == "__main__":
    build(sys.argv[1])
