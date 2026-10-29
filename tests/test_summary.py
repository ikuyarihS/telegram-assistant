import os
import time

from tgkit.digest import is_decision, is_question


def chat(j, prefix):
    return next(c for c in j["chats"] if c["title"].startswith(prefix))


def test_cues():
    assert is_question("Xong chưa ạ")
    assert is_question("Ai biết không?")
    assert not is_question("Ok cảm ơn")
    assert is_decision("Chốt phương án B")
    assert is_decision("We agreed on Monday")
    assert not is_decision("Được ạ")


def test_summary_window_counts(home, tg_json):
    j = tg_json("summary")
    a, b = chat(j, "Dự án Alpha"), chat(j, "Team Beta")
    assert a["messages"] == 7 and b["messages"] == 3
    assert {p["name"]: p["messages"] for p in a["people"]}["Bình Trần"] == 3
    assert [t["root"]["id"] for t in a["threads"]] == [23, 20] or \
        sorted(t["root"]["id"] for t in a["threads"]) == [20, 23]
    assert [q["id"] for q in b["questions"]] == [51, 52]
    assert a["links"][0]["url"] == "https://example.com/design/alpha?v=2"
    assert a["files"][0]["name"] == "Biên bản họp 28-10.pdf"
    assert [q["title"] for q in j["quiet_chats"]] == ["Em Test"]


def test_longer_window(home, tg_json):
    j = tg_json("summary", "--since", "30d")
    assert chat(j, "Team Beta")["messages"] == 4
    assert chat(tg_json("summary", "--since", "all"), "Dự án Alpha")["messages"] == 8


def test_decisions_carry_what_they_reply_to(home, tg_json, tg):
    a = tg_json("summary", "alpha")["chats"][0]
    d = {x["id"]: x for x in a["decisions"]}
    assert d[21]["in_reply_to"]["id"] == 20
    assert d[21]["in_reply_to"]["sender"] == "Bình Trần"
    assert "họp lúc 3h" in d[21]["in_reply_to"]["text"]
    assert d[22]["in_reply_to"] is None
    code, out, err = tg("summary", "alpha")
    assert "Possible decisions" in out and "replying to Bình Trần" in out


def test_stale_copy_is_flagged(home, tg_json, tg):
    assert all(c["stale"] is False for c in tg_json("summary")["chats"])
    old = time.time() - 3 * 86400
    for f in (home / "chats").glob("*/messages.jsonl"):
        os.utime(f, (old, old))
    assert all(c["stale"] is True for c in tg_json("summary")["chats"])
    code, out, err = tg("summary")
    assert "tgkit sync" in out


def test_bad_since_is_explained(home, tg):
    code, out, err = tg("summary", "--since", "banana")
    assert code == 1
    assert "7d" in err and "Traceback" not in err
