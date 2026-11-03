"""0.2.5 gate notes: plain per-chat errors, search --online checks the account, closing
the window ends its own sync, and the guards of _end_own_sync are tested."""

import os
import signal
import subprocess
import sys
import time

import pytest

from test_app_api import TOKEN
from test_v024 import SRC, Account, b_chats, codes, free, rows_by_id, use, wait_job  # noqa: F401
from tgkit import __version__, app as appmod, appserver, applog, net, setupflow
from tgkit import store as storemod
from tgkit.appserver import App
from tgkit.claude import ClaudeRunner
from tgkit.store import lock_status

HOLD = ("import sys,time; sys.path.insert(0, sys.argv[1]); from tgkit.store import SyncLock; "
        "SyncLock(sys.argv[2]).acquire(); print('held', flush=True); time.sleep(120)")
SLEEP = "import time; time.sleep(120)"


def _app(home):
    return App(home, claude=ClaudeRunner(cmd=[]), token=TOKEN)


def _holder(home, token):
    env = dict(os.environ, **{storemod.OWNER_ENV: token})
    child = subprocess.Popen([sys.executable, "-c", HOLD, SRC, str(home)], stdout=subprocess.PIPE,
                             text=True, env=env)
    assert child.stdout.readline().strip() == "held"
    return child


def _reap(*procs):
    for p in procs:
        if p.poll() is None:
            p.kill()
        p.wait(timeout=20)


def test_version_is_025():  # 0.2.6 and later keep every 0.2.5 test
    assert tuple(int(x) for x in __version__.split(".")) >= (0, 2, 5)


# ------------------------------------------------------------------ NOTE 1


def test_note1_a_failed_chat_says_plain_words_and_logs_the_kind(codes, monkeypatch):
    home = codes
    use(monkeypatch, Account(101, b_chats(), fail={5002}))
    tg = setupflow.TelegramSetup(home)
    tg.add(["-1005002"], 1000)
    row = rows_by_id(wait_job(tg))["-1005002"]
    assert row["state"] == "error" and row["message"] == "Could not read any messages from 'B two'."
    assert "RuntimeError" not in row["message"] + (row.get("hint") or "")
    log = applog.log_path(home).read_text(encoding="utf-8")
    assert "RuntimeError: boom-fetch" in log  # the kind and detail are kept for whoever helps


def test_note1_no_user_line_in_net_shows_an_exception_class():
    text = open(net.__file__, encoding="utf-8").read()
    shown = [ln for ln in text.splitlines()
             if "type(exc).__name__" in ln or "type(failure).__name__" in ln]
    assert all("media_error" in ln for ln in shown), shown  # only the stored media note keeps it


# ------------------------------------------------------------------ NOTE 2


class _Counting:
    """A client that counts every call that would search Telegram."""

    def __init__(self, inner):
        self.inner, self.calls = inner, []

    def __getattr__(self, name):
        attr = getattr(self.inner, name)
        if name in ("get_dialogs", "get_entity", "iter_messages"):
            def counted(*a, **k):
                self.calls.append(name)
                return attr(*a, **k)
            return counted
        return attr


def _counting(monkeypatch, home, me_id):
    client = _Counting(Account(me_id, b_chats()).client(home, None))
    monkeypatch.setattr(net, "make_client", lambda base, cfg: client)
    return client


def test_note2_online_search_through_another_login_searches_nothing(codes, tg, monkeypatch):
    home = codes
    client = _counting(monkeypatch, home, 202)  # the chats shown are An Nguyễn's (101)
    def data():  # the lock files are the client's own (one client at a time), not data
        return {p: p.read_bytes() for p in home.rglob("*")
                if p.is_file() and "logs" not in p.parts and p.suffix != ".lock"}
    before = data()
    code, out, err = tg("search", "hello", "--online")
    text = out + err
    assert code != 0 and client.calls == []
    assert "Nothing was searched" in text and "Acc202" in text and "An Nguyễn" in text
    assert "tgkit sync" in text  # what to do, in plain words
    assert data() == before  # read-only: nothing written, switched or moved


def test_note2_online_search_with_the_shown_account_still_searches(codes, tg, monkeypatch):
    client = _counting(monkeypatch, codes, 101)
    code, out, err = tg("search", "hello", "--online")
    assert "Nothing was searched" not in out + err
    assert "get_dialogs" in client.calls
    assert "ValueError" not in err  # a chat Telegram cannot open is skipped in plain words


# ------------------------------------------------------------------ NOTE 3


def test_note3_close_ends_the_windows_own_sync_and_frees_the_lock(home):
    child = _holder(home, "own-close")
    try:
        app = _app(home)
        app._proc, app._proc_owner = child, "own-close"  # as App.sync keeps them
        app.close()
        assert child.poll() is not None
        end = time.monotonic() + 10
        while not free(home) and time.monotonic() < end:
            time.sleep(0.05)
        assert free(home)
    finally:
        _reap(child)


def test_note3_close_never_ends_another_programs_sync(home):
    foreign = _holder(home, "someone-else")
    launcher = subprocess.Popen([sys.executable, "-c", SLEEP])  # the window's own, lock not proved
    try:
        app = _app(home)
        app._proc, app._proc_owner = launcher, "mine"
        app.close()
        assert launcher.poll() is not None  # its own launcher, by the handle it holds
        assert foreign.poll() is None and lock_status(home)["held"]  # never another program
        app._proc = None
        app.close()  # nothing of its own running: nothing ends
        assert foreign.poll() is None
    finally:
        _reap(foreign, launcher)


def test_note3_the_app_closes_its_sync_however_it_ends(home, monkeypatch):
    closed = []
    monkeypatch.setattr(App, "close", lambda self: closed.append(self.home))
    monkeypatch.setattr(appmod, "_stop_on_sigterm", lambda: None)

    def interrupted(_):
        raise KeyboardInterrupt
    monkeypatch.setattr(appmod.time, "sleep", interrupted)
    assert appmod.run(["--no-window"]) == 0  # Ctrl+C, a closed window and SIGTERM all end here
    assert closed == [home]


@pytest.mark.skipif(os.name != "nt", reason="the job object is Windows only")
def test_note3_a_killed_window_takes_its_sync_with_it(tmp_path):
    # The server is killed (no finally runs): its sync and the process below it end too.
    code = ("import subprocess,sys,time; sys.path.insert(0, sys.argv[1]); from tgkit import appserver; "
            "c = subprocess.Popen([sys.executable, '-c', \"import os,time; print(os.getpid(), flush=True); "
            "time.sleep(120)\"], stdout=subprocess.PIPE, text=True); "
            "ok = appserver._end_with_server(c); inner = c.stdout.readline().strip(); "
            "print(c.pid, inner, ok, flush=True); time.sleep(120)")
    server = subprocess.Popen([sys.executable, "-c", code, SRC], stdout=subprocess.PIPE, text=True)
    pids = []
    try:
        line = server.stdout.readline().split()
        pids = [int(line[0]), int(line[1])]
        assert line[2] == "True"
        server.kill()  # like the probe: the server killed, nothing of its own runs
        server.wait(timeout=20)
        end = time.monotonic() + 10
        while any(_alive(p) for p in pids) and time.monotonic() < end:
            time.sleep(0.1)
        assert not any(_alive(p) for p in pids)
    finally:
        _reap(server)
        for p in pids:  # only the pids this test started
            if _alive(p):
                os.kill(p, signal.SIGTERM)


def _alive(pid: int) -> bool:
    import ctypes  # noqa: PLC0415

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = ctypes.c_void_p
    h = k32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
    if not h:
        return False
    try:
        return k32.WaitForSingleObject(ctypes.c_void_p(h), 0) == 0x102  # WAIT_TIMEOUT: still running
    finally:
        k32.CloseHandle(ctypes.c_void_p(h))


# ------------------------------------------------------------------ NOTE 4


class _Launcher:
    pid = -1

    def __init__(self):
        self.killed = False

    def poll(self):
        return 0 if self.killed else None

    def kill(self):
        self.killed = True


def _end(monkeypatch, home, st, mine, now=None):
    """Run _end_own_sync with a launcher stand-in; returns (pids it ended, launcher)."""
    ended = []
    st = {"held": True, **st}  # lock_status always says whether the lock is held (0.2.6 checks it)
    now = None if now is None else {"held": True, **now}
    monkeypatch.setattr(appserver.os, "kill", lambda pid, sig: ended.append(pid))
    monkeypatch.setattr(appserver, "lock_status", lambda h: now if now is not None else st)
    app = _app(home)
    app._proc, app._proc_owner = _Launcher(), mine
    app._end_own_sync(st)
    return ended, app._proc


def test_note4_end_own_sync_ends_the_holder_its_token_proves(home, monkeypatch):
    ended, launcher = _end(monkeypatch, home, {"pid": 4242, "owner": "tok"}, "tok")
    assert ended == [4242] and launcher.killed  # the holder below the launcher, and the launcher


def test_note4_end_own_sync_never_ends_a_holder_it_cannot_prove(home, monkeypatch):
    ended, launcher = _end(monkeypatch, home, {"pid": 4242, "owner": "someone-else"}, "tok")
    assert ended == [] and launcher.killed  # another token: only its own launcher
    ended, _ = _end(monkeypatch, home, {"pid": 4242, "owner": "tok"}, None)
    assert ended == []  # no token of its own: proves nothing
    ended, _ = _end(monkeypatch, home, {"pid": 4242, "owner": "tok"}, "tok",
                    now={"pid": 4243, "owner": "someone-else"})
    assert ended == []  # the lock changed hands since it was read: not ours any more
    ended, _ = _end(monkeypatch, home, {"pid": -1, "owner": "tok"}, "tok")
    assert ended == []  # the holder is the launcher itself: ended by its handle only
