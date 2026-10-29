import hashlib

from tgkit.tasks import detect


def digest_of(home):
    return {p.parent.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (home / "chats").glob("*/messages.jsonl")}


def test_detect_cues():
    assert detect("@an_test nhờ em gửi biên bản họp trước thứ 6 nhé")["due"] == "trước thứ 6"
    assert detect("Please review the API spec by Friday @chi_test")["assignees"] == ["chi_test"]
    assert detect("TODO: cập nhật tài liệu")
    assert detect("can lam xong bao cao")
    assert detect("Được ạ, em đồng ý") is None
    assert detect("Hôm nay ăn trưa ở đâu?") is None
    assert detect("ok") is None


def test_tasks_found_and_listed(home, tg, tg_json):
    before = digest_of(home)
    j = tg_json("tasks")
    by_msg = {t["msg_id"]: t for t in j["tasks"]}
    assert set(by_msg) == {23, 26, 50, 53}
    assert by_msg[23]["for_me"] is True
    assert by_msg[26]["for_me"] is False
    assert j["tasks"][0]["msg_id"] == 23, "tasks for you come first"
    code, out, err = tg("tasks")
    assert code == 0 and "for you" in out
    assert digest_of(home) == before


def test_done_note_add_drop_persist(home, tg, tg_json):
    before = digest_of(home)
    tasks = {t["msg_id"]: t["id"] for t in tg_json("tasks")["tasks"]}
    assert tg("tasks", "done", str(tasks[23]))[0] == 0
    assert tg("tasks", "drop", str(tasks[50]))[0] == 0
    assert tg("tasks", "note", str(tasks[26]), "đã", "nhắc")[0] == 0
    assert tg("tasks", "add", "Gọi khách hàng", "--due", "thứ 2")[0] == 0
    allt = {t["id"]: t for t in tg_json("tasks", "all")["tasks"]}
    assert allt[tasks[23]]["status"] == "done"
    assert allt[tasks[50]]["status"] == "dropped"
    assert allt[tasks[26]]["notes"][0]["text"] == "đã nhắc"
    assert any(t["text"] == "Gọi khách hàng" and t["due"] == "thứ 2" for t in allt.values())
    open_now = {t["msg_id"] for t in tg_json("tasks")["tasks"]}
    assert 23 not in open_now and 50 not in open_now
    assert len(tg_json("tasks", "all")["tasks"]) == 5, "a rescan must not duplicate"
    assert tg("tasks", "undo", str(tasks[23]))[0] == 0
    assert 23 in {t["msg_id"] for t in tg_json("tasks")["tasks"]}
    assert digest_of(home) == before


def test_mine(home, tg_json):
    assert [t["msg_id"] for t in tg_json("tasks", "mine")["tasks"]] == [23]


def test_damaged_task_file_is_left_alone(home, tg):
    (home / "tasks.json").write_text("{broken", encoding="utf-8")
    code, out, err = tg("tasks")
    assert code == 1
    assert "tasks.json" in err and "Traceback" not in err
    assert (home / "tasks.json").read_text(encoding="utf-8") == "{broken"


def test_bad_task_number(home, tg):
    code, out, err = tg("tasks", "done", "999")
    assert code == 1 and "no task number 999" in err
