"""The three open gate notes: false success on a bad task number, URL "?" counted as a
question, and two syncs at once."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tgkit.digest import is_question
from tgkit.errors import UserError
from tgkit.store import LOCK_NAME, SyncLock

from test_sync_guard import ALPHA, FakeClient, Msg, ids, use


def test_tasks_done_with_one_bad_number_changes_nothing(home, tg):
    assert tg("tasks")[0] == 0
    before = (home / "tasks.json").read_bytes()
    code, out, err = tg("tasks", "done", "1", "99")
    assert code == 1
    assert "now done" not in out
    assert "99" in err
    assert (home / "tasks.json").read_bytes() == before
    code, out, err = tg("tasks", "done", "1", "2")
    assert code == 0 and "Task 1 is now done." in out and "Task 2 is now done." in out


def test_question_mark_inside_a_link_is_not_a_question():
    assert not is_question("Link thiết kế: https://example.com/design/alpha?v=2.")
    assert not is_question("xem www?ok")
    assert is_question("Xong chưa?")
    assert is_question("Có thấy https://example.com/a?b=1 không?")
    assert is_question("Ai biết không ?")


def test_question_mark_glued_to_a_link_end_is_a_question():
    """Gate NOTE 3: the "?" after a link is the sender's, the one inside is the link's."""
    assert is_question("see http://a.b/c?d=e?")
    assert is_question("Đã xem https://example.com/a?b=1?")
    assert not is_question("see http://a.b/c?d=e")
    assert not is_question("see http://a.b/c?d=e.")


def test_summary_questions_skip_links(home, tg_json):
    dg = tg_json("summary", "alpha", "--since", "30d")
    ids_ = [q["id"] for q in dg["chats"][0]["questions"]]
    assert 25 not in ids_ and 20 in ids_


def test_second_lock_is_refused_and_released(home):
    first = SyncLock(home).acquire()
    with pytest.raises(UserError) as exc:
        SyncLock(home).acquire()
    assert "already running" in exc.value.message
    first.release()
    with SyncLock(home):
        assert (home / LOCK_NAME).exists()
    assert free(home)


def free(home) -> bool:
    try:
        SyncLock(home).acquire().release()
    except UserError:
        return False
    return True


def test_lock_file_left_by_a_dead_process_does_not_block(home):
    (home / LOCK_NAME).write_text(json.dumps({"pid": 2 ** 22 + 12345, "started": "2026-01-01T00:00:00+00:00"}),
                                  encoding="utf-8")
    with SyncLock(home) as lock:
        assert lock.held
        assert json.loads((home / LOCK_NAME).read_text(encoding="utf-8"))["pid"] == os.getpid()
        assert not free(home)


def test_an_old_running_sync_is_never_taken_over(home):
    """Gate B-3: a sync running past hours keeps its lock; its release frees only its own."""
    a = SyncLock(home).acquire()
    (home / LOCK_NAME).write_text(json.dumps({"pid": os.getpid(), "started": "2020-01-01T00:00:00+00:00"}),
                                  encoding="utf-8")
    with pytest.raises(UserError):
        SyncLock(home).acquire()
    a.release()
    a.release()  # twice is harmless
    c = SyncLock(home).acquire()
    assert not free(home)
    c.release()


def test_lock_is_held_across_processes_and_freed_when_one_dies(home, tmp_path):
    src = str(Path(__file__).resolve().parents[1] / "src")
    code = ("import sys; sys.path.insert(0, sys.argv[1]); from tgkit.store import SyncLock; "
            "SyncLock(sys.argv[2]).acquire(); print('held', flush=True); "
            "sys.stdin.readline(); sys.exit(int(sys.argv[3]))")
    child = subprocess.Popen([sys.executable, "-c", code, src, str(home), "0"],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "held"
        assert not free(home)
    finally:
        child.stdin.close()  # the child ends WITHOUT release(): the system frees the lock
        child.wait(timeout=20)
    assert free(home)


def test_sync_refuses_while_another_runs(home, tg, monkeypatch):
    before = ids(home)
    made = use(monkeypatch, FakeClient({ALPHA: [Msg(30)]}))
    with SyncLock(home):
        code, out, err = tg("sync", "alpha")
        assert code == 1 and "already running" in err
        assert made == []
        code, out, err = tg("sync", "alpha", "--dry-run")
        assert code == 0, err
    assert ids(home) == before
    code, out, err = tg("sync", "alpha")
    assert code == 0, err
    assert ids(home)[0] == 30
    assert free(home)
