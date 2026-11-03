"""0.2.4 gate blockers: account switch on a folder upgraded from 0.2.3, a stuck sync
lock with Stop and retry, per-chat progress and errors for the first copy of ticked
chats, lists that follow the logged-in account, and the lock order.

Telegram is a stand-in (two fake accounts, 101 and 202); nothing touches the network.
The `home` fixture is the 0.2.3 layout: config.json "me" = 101 and no account.json."""

import asyncio
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from test_app_api import TOKEN, call
from tgkit import account, config, net, setupflow
from tgkit import store as storemod
from tgkit.appserver import App, serve
from tgkit.claude import ClaudeRunner
from tgkit.errors import UserError
from tgkit.store import LOCK_NAME, SessionLock, Store, SyncLock, lock_status

SRC = str(Path(__file__).resolve().parents[1] / "src")


class Channel:
    def __init__(self, ident, title):
        self.id, self.title, self.username = ident, title, None
        self.megagroup, self.broadcast, self.participants_count = True, False, 3


class Msg:
    def __init__(self, ident, text="hello"):
        self.id, self.message = ident, text
        self.date = datetime.now(timezone.utc) - timedelta(minutes=5)
        self.reply_to_msg_id = self.sender = self.grouped_id = self.media = None
        self.sender_id, self.views, self.forwards = 77, None, None


class Dlg:
    def __init__(self, ent):
        self.entity, self.id, self.name = ent, int(f"-100{ent.id}"), ent.title
        self.is_group = self.is_channel = True


class Account:
    """One Telegram account, as Telethon shows it. `fail` chats raise while fetched;
    `hang` chats yield their first message, then wait until `release` is set."""

    def __init__(self, me_id, chats, fail=(), hang=(), authorized=True, drop=()):
        self.me_id, self.chats = me_id, chats
        self.fail, self.hang, self.drop = set(fail), set(hang), set(drop)
        self.authorized = authorized
        self.release = threading.Event()

    def client(self, base, cfg):
        return _Client(self, base)


class _Client:
    def __init__(self, acc, base):
        self.acc = acc
        Path(base, "tgkit.session").touch()

    async def connect(self):
        pass

    async def disconnect(self):
        pass

    async def is_user_authorized(self):
        return self.acc.authorized

    async def send_code_request(self, phone):
        return type("Sent", (), {"phone_code_hash": "h"})()

    async def sign_in(self, **kw):
        self.acc.authorized = True

    async def get_me(self):
        return type("User", (), {"id": self.acc.me_id, "first_name": f"Acc{self.acc.me_id}",
                                 "last_name": "", "username": None})()

    async def get_dialogs(self):
        return []

    def iter_dialogs(self, limit=None):
        async def gen():
            for cid, (title, _) in self.acc.chats.items():
                yield Dlg(Channel(cid, title))
        return gen()

    async def get_entity(self, target):
        for cid, (title, _) in self.acc.chats.items():
            if str(target) in (str(cid), f"-100{cid}"):
                return Channel(cid, title)
        raise ValueError(target)

    def iter_messages(self, entity, min_id=0, limit=None, search=None):
        acc, msgs = self.acc, [m for m in self.acc.chats[entity.id][1] if m.id > min_id]

        async def gen():
            if entity.id in acc.fail:
                raise RuntimeError("boom-fetch")
            for n, m in enumerate(msgs[: limit or None]):
                if n == 1 and entity.id in acc.drop:
                    raise ConnectionError("network dropped")  # after the first message
                if n == 1 and entity.id in acc.hang:
                    while not acc.release.is_set():
                        await asyncio.sleep(0.02)
                yield m
        return gen()

    async def download_media(self, msg, file=None):
        return None


def b_chats():
    return {5001: ("B one", [Msg(3), Msg(2), Msg(1)]), 5002: ("B two", [Msg(9), Msg(8)]),
            5003: ("B three", [Msg(7)])}


def names(home):
    return sorted(d.name for d in Store(home).chat_dirs())


def snapshot(home, sub="chats"):
    root = Path(home) / sub
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def free(home, cls=SyncLock) -> bool:
    try:
        cls(home).acquire().release()
        return True
    except UserError:
        return False


@pytest.fixture
def codes(home):
    config.put(home, api_id="1234567", api_hash="0" * 32)
    return home


def use(monkeypatch, acc):
    monkeypatch.setattr(net, "make_client", acc.client)
    return acc


def wait_job(tg, timeout=10.0):
    end = time.monotonic() + timeout
    while tg.adding and time.monotonic() < end:
        time.sleep(0.02)
    assert not tg.adding
    return tg.add_state()


def rows_by_id(state):
    return {r["id"]: r for r in state["rows"]}


# ------------------------------------------------------------------ BLOCKER 1


def test_b1_upgraded_folder_logout_keep_then_login_b_moves_a_aside(codes, monkeypatch):
    home = codes
    assert not (home / "account.json").exists()  # the 0.2.3 layout
    a_names, a_bytes = names(home), snapshot(home)
    (home / "tgkit.session").touch()  # logged in as A
    res = account.logout(home, "keep", server=lambda b, c: True)
    assert res["telegram"] and names(home) == a_names  # kept, still shown until another login
    assert account.owner(home)["id"] == 101  # who they belong to is not forgotten

    use(monkeypatch, Account(202, b_chats(), authorized=False))
    tg = setupflow.TelegramSetup(home)
    assert tg.phone("+84912345678")["step"] == "code"
    done = tg.code("12345")
    assert done["switched"] and done["parked"] and done["comes_back"]
    assert names(home) == [] and account.owner(home)["id"] == 202  # A's chats are not B's
    assert snapshot(home, "accounts/101/chats") == a_bytes

    st = Store(home)  # a sync now gets B's chats only, A's stay byte for byte
    assert net.run(net.sync_chats(net.Ctx(home, st, config.load(home)), st.chat_dirs(), quiet=True)) == 0
    assert snapshot(home, "accounts/101/chats") == a_bytes

    use(monkeypatch, Account(101, {}))  # back to A: its chats come back
    assert tg.dialogs()["restored"]
    assert names(home) == a_names and snapshot(home) == a_bytes


# ------------------------------------------------------------------ BLOCKER 2


def test_b2_lock_status_heartbeat_and_dead_pid(home, monkeypatch):
    lock = SyncLock(home, "sync").acquire()
    try:
        st = lock_status(home)
        assert st["held"] and st["mine"] and st["alive"] and not st["stuck"] and st["what"] == "sync"
        lock._info["beat"] = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        lock._write()
        st = lock_status(home)
        assert st["stuck"] and st["idle"] >= 590  # held, no heartbeat for 10 minutes
        lock.beat(force=True)
        assert not lock_status(home)["stuck"]
    finally:
        lock.release()
    # a lock file left by a process that ended: not held, nothing stuck, the next sync runs
    (home / LOCK_NAME).write_text(json.dumps({"pid": 2 ** 22 + 12345, "beat": "2020-01-01T00:00:00+00:00"}),
                                  encoding="utf-8")
    st = lock_status(home)
    assert not st["held"] and not st["stuck"]
    assert free(home)


def test_b2_stop_and_retry_cancels_a_hung_copy_in_process(codes, monkeypatch):
    home = codes
    before = snapshot(home)
    acc = use(monkeypatch, Account(101, b_chats(), hang={5001}))
    app = App(home, claude=ClaudeRunner(cmd=[]), token=TOKEN)
    app.tg.add(["-1005001"], 1000)
    end = time.monotonic() + 10
    while rows_by_id(app.tg.add_state())["-1005001"]["state"] != "copying" and time.monotonic() < end:
        time.sleep(0.02)
    monkeypatch.setattr(storemod, "STUCK_AFTER", -1)  # as if no heartbeat for minutes
    info = app.lock_info()
    assert info["held"] and info["stuck"] and info["can_stop"]
    assert app.stop_lock()["ok"]
    assert free(home) and free(home, SessionLock) and not app.tg.adding
    row = rows_by_id(app.tg.add_state())["-1005001"]
    assert row["state"] == "stopped" and row["message"]
    assert snapshot(home) == before  # nothing deleted, nothing half-written in the chats
    acc.release.set()
    app.tg.add(["-1005001"], 1000, keep=True)  # ... and retry
    assert rows_by_id(wait_job(app.tg))["-1005001"]["state"] == "done"


def test_b2_stop_and_retry_ends_the_windows_own_hung_sync_child(home):
    code = ("import sys,time; sys.path.insert(0, sys.argv[1]); from tgkit.store import SyncLock; "
            "SyncLock(sys.argv[2]).acquire(); print('held', flush=True); time.sleep(120)")
    # as App.sync starts it: the owner token in the env (a venv launcher puts the lock
    # holder one process below child, so the token, not child.pid, proves it is ours)
    env = dict(os.environ, **{storemod.OWNER_ENV: "own-b2"})
    child = subprocess.Popen([sys.executable, "-c", code, SRC, str(home)], stdout=subprocess.PIPE, text=True,
                             env=env)
    try:
        assert child.stdout.readline().strip() == "held"
        app = App(home, claude=ClaudeRunner(cmd=[]), token=TOKEN)
        app._proc, app._proc_owner = child, "own-b2"  # as App.sync keeps them
        info = app.lock_info()
        assert info["held"] and info["can_stop"] and info["owner"] == "own-b2"
        assert app.stop_lock()["ok"] and free(home)
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=20)


def test_b2_another_programs_lock_is_reported_never_ended(home, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", SRC)  # the window's `python -m tgkit sync` child finds this tgkit
    code = ("import sys,time; sys.path.insert(0, sys.argv[1]); from tgkit.store import SyncLock; "
            "SyncLock(sys.argv[2]).acquire(); print('held', flush=True); sys.stdin.readline()")
    child = subprocess.Popen([sys.executable, "-c", code, SRC, str(home)],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "held"
        app = App(home, claude=ClaudeRunner(cmd=[]), token=TOKEN)
        ev = list(app.sync())[-1]  # the window's sync meets the busy lock
        assert ev["type"] == "done" and ev["code"] and ev["lock"]["held"] and not ev["lock"]["can_stop"]
        with pytest.raises(UserError) as info:
            app.stop_lock()
        assert "Another Telegram Assistant window" in info.value.message
        assert child.poll() is None  # still running: Telegram Assistant never stops another program
    finally:
        child.stdin.close()
        child.wait(timeout=20)


# ------------------------------------------------------------------ BLOCKER 3 and 4


def test_b3_one_failed_chat_keeps_its_error_and_retries_alone(codes, monkeypatch):
    home = codes
    acc = use(monkeypatch, Account(101, b_chats(), fail={5002}))
    tg = setupflow.TelegramSetup(home)
    tg.add(["-1005001", "-1005002", "-1005003"], 1000)
    st = wait_job(tg)
    rows = rows_by_id(st)
    assert rows["-1005001"]["state"] == "done" and rows["-1005001"]["count"] == 3
    assert rows["-1005003"]["state"] == "done"
    assert rows["-1005002"]["state"] == "error" and "B two" in rows["-1005002"]["message"]
    assert rows["-1005002"]["hint"] and st["added"] == 2 and st["failed"] == 1
    acc.fail.clear()
    tg.add(["-1005002"], 1000, keep=True)  # Retry for that chat alone
    rows = rows_by_id(wait_job(tg))
    assert [rows[k]["state"] for k in ("-1005001", "-1005002", "-1005003")] == ["done"] * 3
    assert len([n for n in names(home) if n.endswith(("5001", "5002", "5003"))]) == 3


def test_b3_a_copy_cut_short_is_an_error_with_retry_not_added(codes, monkeypatch):
    home = codes
    acc = use(monkeypatch, Account(101, b_chats(), drop={5001}))
    tg = setupflow.TelegramSetup(home)
    tg.add(["-1005001"], 1000)
    st = wait_job(tg)
    row = rows_by_id(st)["-1005001"]
    assert row["partial"] and row["count"] == 1 and "older messages are missing" in row["message"] and row["hint"]
    assert st["added"] == 0 and st["failed"] == 1  # never counted as added
    first = next(d for d in Store(home).chat_dirs() if d.name.endswith("5001"))
    assert json.loads((first / "channel.json").read_text(encoding="utf-8"))["partial"]
    # Telegram Assistant closed and opened again: the picker still offers it, marked, tickable
    entry = next(d for d in setupflow.TelegramSetup(home).dialogs()["dialogs"] if d["id"] == "-1005001")
    assert entry["partial"] and not entry["added"]
    acc.drop.clear()
    tg.add(["-1005001"], 1000, keep=True)  # Retry copies the chat again
    row = rows_by_id(wait_job(tg))["-1005001"]
    assert row["state"] == "done" and not row["partial"] and row["count"] == 3
    again = next(d for d in Store(home).chat_dirs() if d.name.endswith("5001"))
    assert "partial" not in json.loads((again / "channel.json").read_text(encoding="utf-8"))
    kept = [p for p in (home / "chats").iterdir() if p.name.startswith(".partial-old-")]
    assert len(kept) == 1 and (kept[0] / "messages.jsonl").exists()  # the cut-short copy is kept


def test_b2_own_sync_is_known_by_its_token_behind_a_launcher(home):
    app = App(home, claude=ClaudeRunner(cmd=[]), token=TOKEN)
    lock = SyncLock(home, "sync")
    monkey = os.environ.get(storemod.OWNER_ENV)
    os.environ[storemod.OWNER_ENV] = "tok123"
    try:
        lock.acquire()
    finally:
        if monkey is None:
            os.environ.pop(storemod.OWNER_ENV, None)
        else:
            os.environ[storemod.OWNER_ENV] = monkey
    try:
        assert lock_status(home)["owner"] == "tok123"
        app._proc = type("P", (), {"pid": -1, "poll": lambda self: None})()  # a launcher, other pid
        app._proc_owner = "tok123"
        assert app._own_child(lock_status(home))
        app._proc_owner = "other"
        assert not app._own_child(lock_status(home))
    finally:
        app._proc = None
        lock.release()


def test_b3_the_window_shows_each_failed_chat_over_http(codes, monkeypatch):
    home = codes
    use(monkeypatch, Account(101, b_chats(), fail={5002}))
    app = App(home, claude=ClaudeRunner(cmd=[]), token=TOKEN)
    httpd = serve(app)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        req = __import__("urllib.request").request.Request(
            base + "/api/setup/add-progress", headers={"X-TGKit-Token": TOKEN, "X-TGKit-Lang": "vi"})
        call(base, "/api/setup/add", {"ids": ["-1005001", "-1005002"], "limit": 1000})
        wait_job(app.tg)
        st = json.loads(call(base, "/api/setup/add-progress")[1])
        rows = rows_by_id(st)
        assert rows["-1005001"]["state"] == "done" and rows["-1005002"]["state"] == "error"
        assert "B two" in rows["-1005002"]["message"] and st["lock"]["held"] is False
        with __import__("urllib.request").request.urlopen(req, timeout=20) as res:
            vi = rows_by_id(json.loads(res.read().decode("utf-8")))["-1005002"]["message"]
        assert vi.startswith("Không đọc được tin nào")  # in the window's language
    finally:
        httpd.shutdown()


def test_b4_progress_row_per_chat_while_the_first_copy_runs(codes, monkeypatch):
    home = codes
    acc = use(monkeypatch, Account(101, b_chats(), hang={5001}))
    tg = setupflow.TelegramSetup(home)
    first = tg.add(["-1005001", "-1005003"], 1000)
    assert first["running"] and {r["state"] for r in first["rows"]} <= {"waiting", "copying"}
    end = time.monotonic() + 10
    while time.monotonic() < end:
        rows = rows_by_id(tg.add_state())
        if (rows["-1005001"]["state"] == "copying" and rows["-1005001"]["count"] >= 1
                and rows["-1005003"]["state"] == "done"):
            break
        time.sleep(0.02)
    # 0.2.6: chats are copied a few at once, so a slow chat never holds up the next one
    assert rows["-1005001"]["state"] == "copying" and rows["-1005001"]["count"] == 1
    assert rows["-1005001"]["name"] == "B one" and rows["-1005003"]["state"] == "done"
    lock = lock_status(home)
    assert lock["held"] and lock["what"].startswith("add: B ")  # the heartbeat says what it does
    acc.release.set()
    rows = rows_by_id(wait_job(tg))
    assert rows["-1005001"] == {**rows["-1005001"], "state": "done", "count": 3}
    assert rows["-1005003"]["state"] == "done"


# ------------------------------------------------------------------ BLOCKER 5 and NOTE 1


def test_b5_picker_switch_shows_only_the_new_accounts_lists(codes, monkeypatch):
    home = codes
    (home / "tasks.json").write_text(json.dumps({"tasks": []}), encoding="utf-8")
    use(monkeypatch, Account(202, b_chats()))
    app = App(home, claude=ClaudeRunner(cmd=[]), token=TOKEN)
    httpd = serve(app)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        assert json.loads(call(base, "/api/state")[1])["chats"]  # A's chats before any Telegram call
        r = json.loads(call(base, "/api/setup/dialogs")[1])
        assert r["switched"] and r["parked"] and {d["name"] for d in r["dialogs"]} == {"B one", "B two", "B three"}
        s = json.loads(call(base, "/api/state")[1])
        assert s["chats"] == [] and s["account"] == "Acc202"  # the sidebar has only B's (none yet)
        assert not (home / "tasks.json").exists() and (home / "accounts" / "101" / "tasks.json").exists()
    finally:
        httpd.shutdown()
    js = (Path(SRC) / "tgkit" / "web" / "static" / "app.js").read_text(encoding="utf-8")
    body = js[js.index("function onAccountSwitch"):][:200]
    assert "newConversation()" in body and "refresh()" in body
    assert "if (r.switched) {" in js[js.index("async function loadDialogs"):][:900]


def test_note1_window_start_switches_to_the_session_account(codes, monkeypatch):
    home = codes
    a_bytes = snapshot(home)
    use(monkeypatch, Account(202, b_chats()))
    (home / "tgkit.session").touch()
    app = App(home, claude=ClaudeRunner(cmd=[]), token=TOKEN)
    res = app.account_check()
    assert res["checked"] and res["switched"] and res["me"]["id"] == 202
    assert names(home) == [] and snapshot(home, "accounts/101/chats") == a_bytes
    assert free(home) and free(home, SessionLock)
    assert app.account_check()["switched"] is False  # the same account again: nothing moves


def test_note1_window_start_skips_quietly_when_not_logged_in(codes, monkeypatch):
    use(monkeypatch, Account(202, b_chats(), authorized=False))
    (codes / "tgkit.session").touch()
    res = App(codes, claude=ClaudeRunner(cmd=[]), token=TOKEN).account_check()
    assert res["checked"] is False and names(codes)


# ------------------------------------------------------------------ NOTE 2 and NOTE 3


def test_note2_a_move_that_fails_puts_back_and_says_so(home, monkeypatch):
    real, calls = os.replace, []

    def flaky(src, dst):
        calls.append(src)
        if len(calls) == 2:
            raise PermissionError(13, "Access is denied")
        return real(src, dst)
    monkeypatch.setattr(account.os, "replace", flaky)
    a_names = names(home)
    with pytest.raises(UserError) as info:
        account.adopt(home, {"id": 202, "name": "B", "username": None})
    msg = info.value.message
    assert "could not move" in msg and "Nothing was deleted" in msg and "reach Telegram" not in msg
    assert names(home) == a_names and not any((home / "accounts" / "101").rglob("messages.jsonl"))
    assert setupflow._friendly(info.value) is info.value
    assert "reach Telegram" not in setupflow._friendly(PermissionError(13, "denied")).message


def test_note3_sync_lock_first_then_session_lock(codes, monkeypatch):
    home = codes
    order = []
    real = SyncLock.acquire

    def rec(self):
        if self.name in ("sync.lock", "session.lock"):
            order.append(self.name)
        return real(self)
    monkeypatch.setattr(SyncLock, "acquire", rec)
    use(monkeypatch, Account(101, b_chats()))
    tg = setupflow.TelegramSetup(home)
    tg.dialogs()
    assert order == ["sync.lock", "session.lock"]
    order.clear()
    tg.add(["-1005003"], 10)
    wait_job(tg)
    assert order == ["sync.lock", "session.lock"] * 2  # listing, then copying
    order.clear()
    st = Store(home)
    net.run(net.sync_chats(net.Ctx(home, st, config.load(home)), st.chat_dirs(), quiet=True))
    assert order == ["sync.lock", "session.lock"]
    doc = (Path(SRC).parent / "docs" / "MAINTENANCE.md").read_text(encoding="utf-8")
    assert "`SyncLock` first, then `SessionLock`" in doc
