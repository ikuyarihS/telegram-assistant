"""0.2.6: Telegram Assistant. Text first and files after, 3 chats at once on one client,
FloodWait waited out, Stop at any time, the Files choice, old staging folders cleaned,
"what do I need to do today", the icon set, and the gate notes of 0.2.5.

Telegram is a stand-in (`Tele`); nothing touches the network."""

import asyncio
import json
import re
import struct
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import fixture_data
from test_app_api import server  # noqa: F401  (the window's server, a fixture)
from test_v024 import Channel, codes, rows_by_id, snapshot, use, wait_job  # noqa: F401
from test_v025 import _Launcher, _app
from tgkit import __version__, appserver, claude, config, engine, intent, net, setupflow
from tgkit import store as storemod
from tgkit.errors import UserError
from tgkit.store import Store, SyncLock

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "src" / "tgkit" / "web"


class Doc:
    def __init__(self, size):
        self.mime_type, self.size, self.attributes = "application/pdf", size, []


class MessageMediaDocument:  # the name media_summary reads
    def __init__(self, size):
        self.document = Doc(size)


class Msg:
    def __init__(self, ident, text="hello", size=None, sender=102):
        self.id, self.message = ident, text
        self.date = datetime.now(timezone.utc) - timedelta(minutes=5)
        self.reply_to_msg_id = self.sender = self.grouped_id = None
        self.media = MessageMediaDocument(size) if size else None
        self.sender_id, self.views, self.forwards = sender, None, None


class FloodErr(Exception):
    def __init__(self, seconds):
        super().__init__(f"wait {seconds}")
        self.seconds = seconds


class Dlg:
    def __init__(self, ent):
        self.entity, self.id, self.name = ent, int(f"-100{ent.id}"), ent.title
        self.is_group = self.is_channel = True


class Tele:
    """One Telegram account. `slow` files wait for `release`; `flood` chats raise one
    FloodWait of that many seconds after their first message; `broken` files fail."""

    def __init__(self, chats, slow=(), flood=None, broken=(), step=0.0):
        self.chats, self.slow, self.flood, self.broken = chats, set(slow), dict(flood or {}), set(broken)
        self.step = step
        self.release = threading.Event()
        self.downloads: list = []
        self.reading = self.peak = self.open = self.peak_open = self.made = 0
        self.asked_offsets: list = []

    def client(self, base, cfg):
        self.made += 1
        Path(base, "tgkit.session").touch()
        return self

    async def connect(self):
        self.open += 1
        self.peak_open = max(self.peak_open, self.open)

    async def disconnect(self):
        self.open -= 1

    async def is_user_authorized(self):
        return True

    async def get_me(self):
        return type("User", (), {"id": 101, "first_name": "An", "last_name": "", "username": "an_test"})()

    async def get_dialogs(self):
        return []

    def iter_dialogs(self, limit=None):
        async def gen():
            for cid, (title, _) in self.chats.items():
                yield Dlg(Channel(cid, title))
        return gen()

    async def get_entity(self, target):
        for cid, (title, _) in self.chats.items():
            if str(target) in (str(cid), f"-100{cid}", f"-{cid}"):
                return Channel(cid, title)
        raise ValueError(target)

    def iter_messages(self, entity, min_id=0, limit=None, search=None, offset_id=0):
        self.asked_offsets.append(offset_id)
        msgs = [m for m in self.chats[entity.id][1] if m.id > min_id and (not offset_id or m.id < offset_id)]
        msgs = msgs[:limit] if limit else msgs

        async def gen():
            self.reading += 1
            self.peak = max(self.peak, self.reading)
            try:
                for n, m in enumerate(msgs):
                    if n == 1 and self.flood.get(entity.id):
                        raise FloodErr(self.flood.pop(entity.id))
                    await asyncio.sleep(self.step)
                    yield m
            finally:
                self.reading -= 1
        return gen()

    async def download_media(self, msg, file=None, progress_callback=None):
        self.downloads.append(msg.id)
        while msg.id in self.slow and not self.release.is_set():
            await asyncio.sleep(0.02)
        if msg.id in self.broken:
            raise RuntimeError("download broke")
        p = Path(file) / f"file-{msg.id}.pdf"
        p.write_bytes(b"%PDF fake")
        return str(p)

    async def get_messages(self, entity, ids=None):
        return [m for m in self.chats[entity.id][1] if m.id in set(ids or [])]


def lines(home, folder):
    p = Path(home) / "chats" / folder / "messages.jsonl"
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def folder_of(home, cid):
    return next(d.name for d in Store(home).chat_dirs() if d.name.endswith(f"-{cid}"))


def wait_for(cond, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def ctx_of(home, **kw):
    return net.Ctx(home, Store(home), config.load(home), **kw)


# ------------------------------------------------------------ 3a text first


def test_text_first_the_next_chat_is_saved_while_a_file_still_downloads(codes, monkeypatch):
    home = codes
    acc = use(monkeypatch, Tele({6001: ("Files", [Msg(3, "report", size=100), Msg(2), Msg(1)]),
                                 6002: ("Words", [Msg(9), Msg(8)])}, slow={3}))
    tg = setupflow.TelegramSetup(home)
    tg.add(["-1006001", "-1006002"], 1000)
    both = wait_for(lambda: all(r["state"] == "done" for r in tg.add_state()["rows"]))
    try:
        assert both  # the text of both chats is saved while the file of the first one waits
        rows = rows_by_id(tg.add_state())
        assert rows["-1006001"]["files_pending"] == 1 and rows["-1006001"]["count"] == 3
        assert tg.add_state()["running"]
        first = lines(home, folder_of(home, 6001))
        assert first[0]["id"] == 3 and first[0]["media_pending"] and not first[0].get("media_path")
        assert [r["id"] for r in lines(home, folder_of(home, 6002))] == [9, 8]  # searchable already
    finally:
        acc.release.set()
    st = wait_job(tg)
    assert rows_by_id(st)["-1006001"]["files_pending"] == 0 and rows_by_id(st)["-1006001"]["files"] == 1
    row = lines(home, folder_of(home, 6001))[0]
    assert row["media_path"].endswith("file-3.pdf") and "media_pending" not in row
    assert (Store(home).chats / row["media_path"]).is_file()


def test_partial_fetch_guard_is_kept_for_sync(home, monkeypatch, capsys):
    # The R4 guard: a sync that reads only the newest part of the new messages commits nothing.
    before = lines(home, "Du_an_Alpha")
    acc = Tele({1001: ("Alpha", [Msg(40), Msg(39), Msg(38)])}, flood={1001: 10_000})
    monkeypatch.setattr(net, "make_client", acc.client)
    monkeypatch.setattr(net, "_flood_error", lambda: FloodErr)
    st = Store(home)
    net.run(net.sync_chats(ctx_of(home), [st.chats / "Du_an_Alpha"], quiet=True))
    assert lines(home, "Du_an_Alpha") == before
    assert "discarding 1 message(s)" in capsys.readouterr().err


# ------------------------------------------------------------ 3b at once, FloodWait


def test_three_chats_at_once_on_one_client(codes, monkeypatch):
    home = codes
    chats = {7000 + i: (f"Chat {i}", [Msg(4), Msg(3), Msg(2), Msg(1)]) for i in range(5)}
    acc = use(monkeypatch, Tele(chats, step=0.03))
    tg = setupflow.TelegramSetup(home)
    tg.add([f"-100{c}" for c in chats], 1000)
    st = wait_job(tg)
    assert all(r["state"] == "done" and r["count"] == 4 for r in st["rows"])
    assert acc.peak == net.CHATS_AT_ONCE == 3  # 3 at a time, never more
    assert acc.peak_open == 1  # always the one client (R14): listing, then copying


def test_a_floodwait_is_waited_out_and_the_read_goes_on(home, monkeypatch):
    slept = []

    async def sleep(s):
        slept.append(s)
    monkeypatch.setattr(net, "_sleep", sleep)
    monkeypatch.setattr(net, "_flood_error", lambda: FloodErr)
    acc = Tele({8001: ("Flood", [Msg(i) for i in range(10, 0, -1)])}, flood={8001: 5})
    got = asyncio.run(net.add_chat(acc, ctx_of(home), Channel(8001, "Flood"), "Flood", 100))
    assert got["messages"] == 10 and not got["partial"]
    assert sum(slept) == 5 + 1  # waited the seconds Telegram asked for, plus one to be safe
    assert acc.asked_offsets == [0, 10]  # and went on from the oldest message read so far
    ids = [r["id"] for r in lines(home, got["folder"])]
    assert ids == sorted(set(ids), reverse=True) and len(ids) == 10


def test_a_floodwait_too_long_ends_that_chat_in_plain_words(home, monkeypatch):
    monkeypatch.setattr(net, "_flood_error", lambda: FloodErr)
    acc = Tele({8002: ("Long", [Msg(3), Msg(2), Msg(1)])}, flood={8002: net.FLOOD_MAX_WAIT + 1})
    got = asyncio.run(net.add_chat(acc, ctx_of(home), Channel(8002, "Long"), "Long", 100))
    assert got["partial"] and got["messages"] == 1  # kept as the flagged seed, Retry copies again
    assert json.loads((Store(home).chats / got["folder"] / "channel.json").read_text("utf-8"))["partial"]


# ------------------------------------------------------------ 3c Files choice


def test_text_only_downloads_no_file_and_says_why(home):
    acc = Tele({8101: ("Docs", [Msg(2, size=10), Msg(1, size=10)])})
    got = asyncio.run(net.add_chat(acc, ctx_of(home, media="none"), Channel(8101, "Docs"), "Docs", 100))
    assert got["files"] == 0 and got["jobs"] == [] and acc.downloads == []
    assert all(r["media_skipped"] == "text only" for r in lines(home, got["folder"]))


def test_small_skips_a_big_file_and_all_takes_it(home):
    big = config.max_media_bytes(home, config.load(home)) + 1
    acc = Tele({8102: ("Big", [Msg(1, size=big)])})
    small = asyncio.run(net.add_chat(acc, ctx_of(home), Channel(8102, "Big"), "Big", 100))
    assert small["files"] == 0 and lines(home, small["folder"])[0]["media_skipped"].startswith("size ")
    acc2 = Tele({8103: ("Big2", [Msg(1, size=big)])})
    every = asyncio.run(net.add_chat(acc2, ctx_of(home, media="all"), Channel(8103, "Big2"), "Big2", 100))
    assert every["files"] == 1


def test_files_setting_round_trip_and_refusal(home):
    app = _app(home)
    assert app.settings()["media"] == "small" and app.settings()["media_modes"] == ["small", "none", "all"]
    assert app.save_settings({"media": "none"})["media"] == "none"
    assert config.media_mode(config.load(home)) == "none"
    try:
        app.save_settings({"media": "huge"})
        raise AssertionError("refused")
    except UserError:
        pass
    try:
        app.setup_add({"ids": ["1"], "files": "huge"})
        raise AssertionError("refused")
    except UserError as exc:
        assert "Files" in exc.message


# ------------------------------------------------------------ retry of files


def test_a_failed_file_is_recorded_then_retried_by_the_next_sync(home, monkeypatch):
    meta = {"id": 6006, "type": "Channel", "title": "Files", "username": None, "megagroup": True}
    row = fixture_data._row(5, 1, 102, "the contract",
                            media={"kind": "MessageMediaDocument", "mime_type": "application/pdf", "size": 10})
    row["media_pending"] = True
    d = fixture_data.write_chat(home, "Files-6006", meta, [row])
    acc = Tele({6006: ("Files", [Msg(5, "the contract", size=10)])}, broken={5})

    async def first():
        ctx = ctx_of(home)
        pool = net.MediaPool(acc, ctx, FloodErr)
        pool.add(d, await net.retry_jobs(acc, ctx, Channel(6006, "Files"), d))
        await pool.finish()
    asyncio.run(first())
    failed = lines(home, "Files-6006")[0]
    assert failed["media_error"] == "RuntimeError: download broke" and failed["media_tries"] == 1
    assert "media_pending" not in failed and failed["text"] == "the contract"  # the text is untouched

    acc.broken.clear()
    for cid, title in ((1001, "Alpha"), (2002, "Beta"), (3003, "Ke toan")):
        acc.chats[cid] = (title, [])
    monkeypatch.setattr(net, "make_client", acc.client)
    st = Store(home)
    net.run(net.sync_chats(ctx_of(home), st.chat_dirs(), quiet=True))
    done = lines(home, "Files-6006")[0]
    assert done["media_path"].endswith("file-5.pdf") and "media_error" not in done and "media_tries" not in done


def test_a_file_that_failed_too_often_is_not_tried_again(home):
    row = {"id": 1, "media": {"kind": "photo"}, "media_error": "x", "media_tries": net.MEDIA_TRIES}
    assert not net._retry_wanted(row)
    assert net._retry_wanted({**row, "media_tries": net.MEDIA_TRIES - 1})


# ------------------------------------------------------------ 3d Stop


def test_stop_mid_copy_keeps_what_was_read_as_a_flagged_partial(home):
    acc = Tele({8201: ("Long", [Msg(i) for i in range(50, 0, -1)])})
    seen = {"n": 0}

    def counted(n):
        seen["n"] = n
    ctx = ctx_of(home, stop=lambda: seen["n"] >= 5)
    got = asyncio.run(net.add_chat(acc, ctx, Channel(8201, "Long"), "Long", 1000, counted))
    assert got["stopped"] and got["partial"] and got["messages"] == 5
    assert json.loads((Store(home).chats / got["folder"] / "channel.json").read_text("utf-8"))["partial"]


def test_stop_button_ends_a_running_copy_cleanly(codes, monkeypatch):
    home = codes
    use(monkeypatch, Tele({8301: ("Slow", [Msg(i) for i in range(200, 0, -1)]), 8302: ("Later", [Msg(1)])},
                          step=0.02))
    app = _app(home)
    app.setup_add({"ids": ["-1008301"], "limit": "all", "files": "none"})
    assert wait_for(lambda: (rows_by_id(app.tg.add_state())["-1008301"]["count"] or 0) > 0)
    res = app.stop_running()
    assert res["stopped"]
    st = wait_job(app.tg)
    row = rows_by_id(st)["-1008301"]
    assert row["state"] == "stopped" and 0 < row["count"] < 200
    assert "Retry" in row["hint"]
    SyncLock(home).acquire().release()  # the lock is free again


def test_stop_of_a_sync_by_the_stop_file_saves_nothing_halfway(home, monkeypatch, capsys):
    stop = storemod.stop_check(home, "tok", every=0)
    assert not stop()
    (home / storemod.STOP_FILE).write_text("someone-else", encoding="utf-8")
    assert not stop()  # another window's stop is not ours
    (home / storemod.STOP_FILE).write_text("tok", encoding="utf-8")
    assert stop()
    before = snapshot(home)
    acc = Tele({1001: ("Alpha", [Msg(40)])})
    monkeypatch.setattr(net, "make_client", acc.client)
    ctx = ctx_of(home, stop=stop)
    assert net.run(net.sync_chats(ctx, Store(home).chat_dirs(), quiet=True)) == 0
    assert "Stopped: 0 new message(s) saved" in capsys.readouterr().out
    assert {k: v for k, v in snapshot(home).items()} == before


def test_the_copying_note_names_no_button_and_a_loaded_page_shows_the_stop():
    js = (WEB / "static" / "app.js").read_text(encoding="utf-8")
    html = (WEB / "index.html").read_text(encoding="utf-8")
    # gate BLOCKER 1: COPYING is also said where no Stop is shown (a login step)
    assert "Stop" not in " ".join(setupflow.COPYING)
    assert "ui.suStop.hidden = !p.running;" in js  # visible the whole time a copy runs
    assert 'id="syncStop"' in html and "ui.syncStop.hidden = false;" in js
    # a page loaded mid-copy or mid-sync follows it (BLOCKER 1 and 2)
    assert 'await suCopyRunning()) return;' in js and '"/api/running"' in js
    assert re.search(r"applyStrings\(\);\s*setBusy\(false\);\s*followRunning\(\);", js)
    assert '"/api/stop"' in js and 'files: suFiles()' in js and 't("progDoneFiles"' in js
    assert 'id="todayBtn"' in html and 'id="todayEmpty"' in html and 'id="mediaMode"' in html
    assert 'name="suFiles"' in html


def test_a_running_copy_is_reported_and_a_file_stop_skipped_shows_on_its_row(codes, monkeypatch):
    # gate BLOCKER 1 (what a page loaded mid-copy asks) and NOTE 1 (a file Stop skipped)
    home = codes
    acc = use(monkeypatch, Tele({8401: ("Slides", [Msg(2, size=100), Msg(1)])}, slow={2}))
    app = _app(home)
    app.setup_add({"ids": ["-1008401"], "limit": "all", "files": "small"})
    try:
        assert wait_for(lambda: rows_by_id(app.tg.add_state())["-1008401"]["state"] == "done")
        run = app.running()
        assert run["copy"] and not run["sync"]["running"]
        assert app.tg.add_state()["running"] and rows_by_id(app.tg.add_state())["-1008401"]["files_pending"] == 1
        assert app.stop_running()["stopped"]
    finally:
        acc.release.set()
    st = wait_job(app.tg)
    row = rows_by_id(st)["-1008401"]
    assert row["state"] == "done" and row["files_skipped"] == 1 and row["files_pending"] == 0
    first = lines(home, folder_of(home, 8401))[0]
    assert first["id"] == 2 and first["media_pending"] and not first.get("media_path")  # the next sync gets it
    assert not app.running()["copy"]


_FAKE_SYNC = """
import sys, time, pathlib
flag = pathlib.Path(sys.argv[1])
print("Checking 2 chats", flush=True)
while not flag.exists():
    time.sleep(0.05)
for i in range(4):
    print(f"Chat {i}: up to date", flush=True)
    time.sleep(0.05)
print("Done: 0 new message(s).", flush=True)
"""


def test_a_sync_runs_on_after_its_page_goes_away_and_is_reported(server, tmp_path, monkeypatch):
    # gate BLOCKER 2: a reload closes the page's stream; the sync must run on (not be
    # killed at its next line) and a page loaded later sees it, then how it ended
    import socket
    import subprocess
    import sys

    app, base = server
    flag = tmp_path / "go.flag"
    real = subprocess.Popen

    def fake(args, **kw):
        return real([sys.executable, "-c", _FAKE_SYNC, str(flag)], **kw)
    monkeypatch.setattr(appserver.subprocess, "Popen", fake)
    host, port = base.split("//")[1].split(":")
    sock = socket.create_connection((host, int(port)), timeout=20)
    sock.sendall(f"POST /api/sync HTTP/1.1\r\nHost: {host}:{port}\r\nX-TGKit-Token: test-token\r\n"
                 "Content-Type: application/json\r\nContent-Length: 2\r\n\r\n{}".encode("ascii"))
    got = b""
    while got.count(b"\n{") < 2 or not got.endswith(b"\n"):
        part = sock.recv(4096)
        if not part:
            break
        got += part
    assert b"\r\n\r\n" in got, got
    seen = [json.loads(x) for x in got.split(b"\r\n\r\n", 1)[1].splitlines() if x.strip()]
    assert [e["type"] for e in seen][:2] == ["start", "running"], seen
    sock.close()  # the page went away (F5, a closed tab)
    assert wait_for(lambda: app.running()["sync"]["line"] == "Checking 2 chats")
    with urllib.request.urlopen(urllib.request.Request(base + "/api/running", headers={"X-TGKit-Token": "test-token"}),
                                timeout=20) as r:
        run = json.loads(r.read())
    assert run["sync"]["running"] and run["sync"]["last"] is None and run["copy"] is False
    flag.write_text("go", encoding="utf-8")
    assert wait_for(lambda: not app.running()["sync"]["running"], 20)
    last = app.running()["sync"]["last"]
    assert last["type"] == "done" and last["code"] == 0 and last["last"] == "Done: 0 new message(s)."


def _ps_exe():
    import shutil
    return shutil.which("powershell") or shutil.which("pwsh")


def test_update_next_to_an_old_folder_points_the_claude_plugin_at_the_new_one(tmp_path):
    # gate NOTE 4: the marketplace "tgkit" was added from the old "TG Kit" folder; the update
    # removes that entry, adds this folder and installs from it. A fake `claude` records it.
    import subprocess

    import pytest
    ps = _ps_exe()
    if not ps:
        pytest.skip("no PowerShell here")
    new_kit = tmp_path / "Telegram Assistant"
    script = r"""
$ErrorActionPreference = 'Stop'
$ast = [System.Management.Automation.Language.Parser]::ParseFile($args[0], [ref]$null, [ref]$null)
$fn = $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Register-ClaudePlugin' }, $true)
if (-not $fn) { 'NO FUNCTION'; exit 3 }
Invoke-Expression $fn[0].Extent.Text
function Say($t) { Write-Host "SAY $t" }
$script:market = @{ tgkit = 'C:\old\TG Kit' }
$script:installedFrom = $script:market['tgkit']
function claude {
    $a = $args -join ' '
    Write-Host "CALL $a"
    $global:LASTEXITCODE = 0
    if ($a -eq 'plugin marketplace remove tgkit') {
        if ($script:market.ContainsKey('tgkit')) { $script:market.Remove('tgkit') } else { $global:LASTEXITCODE = 1 }
    } elseif ($a -like 'plugin marketplace add *') {
        if ($script:market.ContainsKey('tgkit')) { $global:LASTEXITCODE = 1 } else { $script:market['tgkit'] = $args[3] }
    } elseif ($a -eq 'plugin install tgkit@tgkit') {
        $script:installedFrom = $script:market['tgkit']
    }
}
$ok = Register-ClaudePlugin $args[1]
"OK $ok"
"MARKET $($script:market['tgkit'])"
"FROM $($script:installedFrom)"
"""
    probe = tmp_path / "probe.ps1"
    probe.write_text(script, encoding="utf-8-sig")
    out = subprocess.run([ps, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(probe),
                          str(ROOT / "scripts" / "install.ps1"), str(new_kit)],
                         capture_output=True, text=True, timeout=120)
    text = out.stdout
    assert f"MARKET {new_kit}" in text and f"FROM {new_kit}" in text and "OK True" in text, text + out.stderr
    calls = [ln[5:] for ln in text.splitlines() if ln.startswith("CALL ")]
    assert calls == ["plugin marketplace remove tgkit", f"plugin marketplace add {new_kit}",
                     "plugin install tgkit@tgkit"]
    sh = (ROOT / "install.sh").read_text(encoding="utf-8")
    assert "claude plugin marketplace remove tgkit" in sh and 'register_claude_plugin "$KIT"' in sh


# ------------------------------------------------------------ 3e staging


def test_old_staging_folders_are_removed_and_chats_are_not_touched(codes, monkeypatch):
    home = codes
    chats = Store(home).chats
    for name in (".adding-Old-1", ".adding-x.old-20260101-000000"):
        (chats / name).mkdir()
        (chats / name / "messages.jsonl").write_text("{}\n", encoding="utf-8")
    (chats / "adding-not-staging").mkdir()
    before = snapshot(home)
    use(monkeypatch, Tele({8401: ("New", [Msg(1)])}))
    tg = setupflow.TelegramSetup(home)
    tg.add(["-1008401"], 1000)
    wait_job(tg)
    names = {p.name for p in chats.iterdir()}
    assert not any(n.startswith(".adding-") for n in names)
    assert "adding-not-staging" in names
    after = snapshot(home)
    kept = {k: v for k, v in before.items() if not k.startswith(".adding-")}
    assert {k: after[k] for k in kept} == kept  # every committed chat byte for byte


# ------------------------------------------------------------ 2 today


def _today_home(home):
    now = datetime.now(timezone.utc)

    def row(i, secs, sender, text, **kw):
        return {"id": i, "date": (now - timedelta(seconds=secs)).replace(microsecond=0).isoformat(),
                "text": text, "views": None, "forwards": None, "reply_to_msg_id": None,
                "sender_id": sender, "grouped_id": None, "media": None, "media_path": None, **kw}
    fixture_data.write_chat(home, "Chi-7007", {"id": 7007, "type": "User", "title": None, "username": None,
                                               "first_name": "Chi", "last_name": "Test"},
                            [row(1, 30, 102, "Chị đã xem tài liệu chưa?")])
    hop = [row(100 + i, 40 + i, 103, f"Họp lúc 3h hôm nay nhé, phòng {i}") for i in range(12)]
    hop += [row(200 + i, 10 + i % 9, 104, "ok") for i in range(20)]  # newer, not about today
    fixture_data.write_chat(home, "Hop-8008", {"id": 8008, "type": "Channel", "title": "Họp",
                                               "username": "hop_test", "megagroup": True}, hop)
    return home


def test_today_groups_with_chat_sender_time_and_link(home):
    _today_home(home)
    before = snapshot(home, ".")
    data = engine.today(home, Store(home), "vi")
    assert snapshot(home, ".") == before  # read only: nothing is written, tasks.json neither
    g = {x["key"]: x for x in data["groups"]}
    assert set(g) == {"tasks", "asks", "today"} and g["asks"]["title"] == "Đang chờ bạn trả lời"
    ask = next(i for i in g["asks"]["items"] if i["folder"] == "Chi-7007")
    assert ask["chat"] and ask["sender"] and ask["time"] and ask["text"] == "Chị đã xem tài liệu chưa?"
    hop = [i for i in g["today"]["items"] if i["folder"] == "Hop-8008"]
    assert hop and all(i["link"].startswith("https://t.me/hop_test/") and i["sender"] and i["time"]
                       for i in hop)
    assert data["total"] == sum(x["total"] for x in data["groups"])


def test_today_filters_first_then_caps_and_counts_the_rest(home):
    _today_home(home)
    g = {x["key"]: x for x in engine.today(home, Store(home), "en")["groups"]}["today"]
    mine = [i for i in g["items"] if i["folder"] == "Hop-8008"]
    assert g["total"] >= 12 and len(g["items"]) == engine.TODAY_MAX
    assert g["more"] == g["total"] - engine.TODAY_MAX
    assert mine and all("hôm nay" in i["text"] for i in mine)  # the 20 newer "ok" never push them out


def test_today_phrases_vi_en_and_a_search_that_is_not_today():
    for text in ("hôm nay tôi cần làm gì", "Hôm nay tôi cần làm gì?", "hom nay toi can lam gi",
                 "What do I need to do today?", "what do i need to do today"):
        assert intent.parse(text).kind == "today", text
    assert intent.parse("bank today").kind != "today"
    assert intent.parse("find the invoice today").kind != "today"


def test_today_offline_answer_is_a_today_card(home):
    _today_home(home)
    out = engine.answer(home, Store(home), intent.parse("hôm nay tôi cần làm gì"))
    assert out["cards"][0]["type"] == "today" and out["text"]


def test_tgkit_today_json_runs_read_only_for_claude(home, tg, monkeypatch):
    _today_home(home)
    monkeypatch.setenv("TGKIT_READONLY", "1")
    code, out, err = tg("today", "--json")
    assert code == 0, err
    data = json.loads(out)
    assert data["kind"] == "today" and [g["key"] for g in data["groups"]] == ["tasks", "asks", "today"]
    code, out, _ = tg("today")
    assert code == 0 and "Họp" in out
    assert "mcp__tgkit__today" in claude.ALLOWED
    assert "tgkit today --json" in claude.APP_RULES


# ------------------------------------------------------------ 5 gate notes


def test_note1_close_ends_a_holder_only_while_the_lock_is_still_held(home, monkeypatch):
    ended = []
    monkeypatch.setattr(appserver.os, "kill", lambda pid, sig: ended.append(pid))
    monkeypatch.setattr(appserver, "lock_status", lambda h: {"held": False, "pid": 4242, "owner": "tok"})
    app = _app(home)
    app._proc, app._proc_owner = _Launcher(), "tok"
    app._end_own_sync({"held": True, "pid": 4242, "owner": "tok"})
    assert ended == []  # the lock was freed meanwhile: pid 4242 may be another program now


def test_note3_plain_words_without_class_names(tmp_path):
    bad = tmp_path / "x.json"
    bad.write_text("{not json", encoding="utf-8")
    try:
        storemod.read_json(bad, None)
        raise AssertionError("refused")
    except UserError as exc:
        text = exc.message + (exc.hint or "")
        assert "could not be read" in text and not re.search(r"[A-Z][a-z]+Error|Exception", text)
    src = (ROOT / "src" / "tgkit" / "account.py").read_text(encoding="utf-8")
    assert "could not move" in src and "Telegram could not be reached." in src
    cli_src = (ROOT / "src" / "tgkit" / "cli.py").read_text(encoding="utf-8")
    assert "Sorry, something went wrong." in cli_src


# ------------------------------------------------------------ 1, 4, 7 name, icon, version


def test_version_is_026_everywhere():  # 0.2.7 and later keep every 0.2.6 test
    assert tuple(int(x) for x in __version__.split(".")) >= (0, 2, 6)
    assert f'version = "{__version__}"' in (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"] == __version__


def test_the_window_says_telegram_assistant():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    js = (WEB / "static" / "app.js").read_text(encoding="utf-8")
    assert "<title>Telegram Assistant</title>" in html and "TG Kit" not in html and "TG Kit" not in js
    zipper = (ROOT / "scripts" / "make-share-zip.ps1").read_text(encoding="utf-8")
    assert "TelegramAssistant-$Version.zip" in zipper
    assert "TG Kit.lnk" in (ROOT / "scripts" / "install.ps1").read_text(encoding="utf-8")  # the old one goes


def test_icon_set_ico_16_to_256_and_favicon():
    ico = (WEB / "static" / "tgkit.ico").read_bytes()
    reserved, kind, count = struct.unpack("<HHH", ico[:6])
    assert (reserved, kind) == (0, 1) and count == 7
    sizes = sorted((ico[6 + 16 * i] or 256) for i in range(count))
    assert sizes[0] == 16 and sizes[-1] == 256
    for name in ("favicon.png", "icon-256.png"):
        assert (WEB / "static" / name).read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert (ROOT / "assets" / "icon-source.png").is_file() and (ROOT / "scripts" / "make-icons.py").is_file()
    assert {"favicon.png", "icon-256.png", "tgkit.ico"} <= set(appserver.STATIC)


def test_favicon_and_ico_are_served(server):
    _, base = server
    for name, kind in (("favicon.png", "image/png"), ("tgkit.ico", "image/x-icon")):
        with urllib.request.urlopen(f"{base}/static/{name}", timeout=20) as res:
            assert res.status == 200 and res.headers["Content-Type"] == kind
            assert res.read() == (WEB / "static" / name).read_bytes()
