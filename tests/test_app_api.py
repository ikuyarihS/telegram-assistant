"""The app's local API, over real HTTP on 127.0.0.1. No network beyond that, no Claude."""

import json
import shutil
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from tgkit import app as appmod
from tgkit.appserver import App, serve
from tgkit.claude import ClaudeRunner

TOKEN = "test-token"


@pytest.fixture
def server(home):
    app = App(home, claude=ClaudeRunner(cmd=[]), token=TOKEN)
    httpd = serve(app)
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    yield app, base
    httpd.shutdown()


def call(base, path, body=None, token=TOKEN):
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(base + path, data=data, method="GET" if body is None else "POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("X-TGKit-Token", token)
    try:
        with urllib.request.urlopen(req, timeout=20) as res:
            return res.status, res.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


def refused(code, body) -> bool:
    """A problem the user can fix: answered 200 with user_error, so the page's
    browser logs no failed request for it (0.2.3 gate note 3)."""
    if code != 200:
        return False
    try:
        data = json.loads(body)
    except ValueError:
        return False
    return data.get("user_error") is True and data.get("type") == "error" and bool(data.get("message"))


def events(text):
    return [json.loads(x) for x in text.splitlines() if x.strip()]


def ask(base, text, conv=None):
    code, body = call(base, "/api/ask", {"text": text, "conversation": conv})
    assert code == 200
    return events(body)


def test_page_and_static(server):
    _, base = server
    code, html = call(base, "/", token=None)
    assert code == 200 and TOKEN in html and "__TGKIT_TOKEN__" not in html
    assert call(base, "/static/app.js", token=None)[0] == 200
    assert call(base, "/static/../cli.py", token=None)[0] == 404


# ------------------------------------------------------------------ 0.2.4: Select all in the chat picker

APP_JS = Path(appmod.__file__).parent / "web" / "static" / "app.js"
INDEX = Path(appmod.__file__).parent / "web" / "index.html"
DIALOGS = [
    {"id": "1", "name": "Nhóm Alpha", "kind": "group", "added": False},
    {"id": "2", "name": "Team Beta", "kind": "group", "added": False},
    {"id": "3", "name": "Chị Hoa", "kind": "person", "added": False},
    {"id": "4", "name": "Tin tức", "kind": "channel", "added": False},
    {"id": "5", "name": "Nhóm cũ", "kind": "group", "added": True},
]


def pick(script: str):
    """Run the page's TGPick rules in node on DIALOGS (as `D`); the script returns a value."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    code = (f"const P = require(process.argv[1]); const D = {json.dumps(DIALOGS, ensure_ascii=False)};\n"
            f"const out = (() => {{ {script} }})(); process.stdout.write(JSON.stringify(out));")
    r = subprocess.run([node, "-e", code, str(APP_JS)], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_select_all_without_filter_ticks_every_chat_then_clears():
    out = pick("""
        const shown = P.shown(D, "", "all");
        const s0 = P.state(shown);
        P.toggleAll(shown);
        const s1 = P.state(shown), c1 = P.count(D), added = D[4].picked || false;
        P.toggleAll(shown);
        return { n: shown.length, s0, s1, c1, added, s2: P.state(shown), c2: P.count(D) };
    """)
    assert out["n"] == 5 and out["s0"] == "none"
    assert out["s1"] == "all" and out["c1"] == {"picked": 4, "total": 4}
    assert out["added"] is False  # an already added chat is never picked again
    assert out["s2"] == "none" and out["c2"] == {"picked": 0, "total": 4}


def test_select_all_with_filter_ticks_only_the_chats_shown():
    out = pick("""
        const shown = P.shown(D, "  nhom ", "all");  // accents optional, spaces trimmed
        P.toggleAll(shown);
        const all = P.shown(D, "", "all");
        return { names: shown.map((d) => d.name), picked: D.filter((d) => d.picked).map((d) => d.id),
                 shownState: P.state(shown), allState: P.state(all), count: P.count(D) };
    """)
    assert out["names"] == ["Nhóm Alpha", "Nhóm cũ"]
    assert out["picked"] == ["1"]
    assert out["shownState"] == "all" and out["allState"] == "mixed"
    assert out["count"] == {"picked": 1, "total": 4}


def test_select_all_with_kind_chip_and_mixed_state():
    out = pick("""
        D[0].picked = true;
        const groups = P.shown(D, "", "group");
        const before = P.state(groups);
        P.toggleAll(groups);  // mixed: ticks the rest, never clears
        const people = P.shown(D, "", "person");
        return { groups: groups.map((d) => d.id), before, after: P.state(groups),
                 people: people.map((d) => d.id), peopleState: P.state(people), picked: P.count(D).picked };
    """)
    assert out["groups"] == ["1", "2", "5"] and out["before"] == "mixed" and out["after"] == "all"
    assert out["people"] == ["3"] and out["peopleState"] == "none" and out["picked"] == 2


def test_big_pick_note_threshold_and_page_wiring():
    assert pick("return [P.big(30), P.big(31), P.BIG];") == [False, True, 30]
    html = INDEX.read_text(encoding="utf-8")
    assert html.index('id="suPickAll"') < html.index('id="suList"') < html.index('id="suBigNote"')
    js = APP_JS.read_text(encoding="utf-8")
    en, vi = js.split("    vi: {", 1)
    for key in ("suPickAll:", "suPickShown:", "suCount:", "suBig:", "suKindAll:", "suKindGroups:",
                "suKindChannels:", "suKindPeople:", "suShowKind:"):
        assert key in en and key in vi, key
    # Select all only ticks boxes; the copy starts from the Add button alone.
    assert 'api("/api/setup/add"' in js and js.count("/api/setup/add") == 1


# ------------------------------------------------------------------ 0.2.4: one Telegram client, a log, account switch


class SessionClient:
    """Like Telethon: holds tgkit.session (SQLite) in a write transaction while connected,
    so a second client on the same file meets "database is locked"."""

    def __init__(self, base, me_id=101):
        self.path = Path(base) / "tgkit.session"
        self.me_id = me_id
        self.db = None

    async def connect(self):
        import sqlite3
        self.db = sqlite3.connect(self.path, timeout=0)
        self.db.execute("BEGIN IMMEDIATE")
        self.db.execute("CREATE TABLE IF NOT EXISTS s (v)")
        self.db.execute("INSERT INTO s VALUES (1)")

    async def is_user_authorized(self):
        return True

    async def get_me(self):
        return type("User", (), {"id": self.me_id, "first_name": "B", "last_name": "", "username": None})()

    async def disconnect(self):
        if self.db is not None:
            self.db.commit()
            self.db.close()
            self.db = None


def test_two_clients_on_one_session_never_meet_a_locked_database(home, monkeypatch):
    """The 0.2.3 bug: the window listed or added chats while a sync had the login
    file open; SQLite said OperationalError. Now one client at a time, the other waits."""
    import asyncio

    from tgkit import net
    from tgkit.store import Store
    monkeypatch.setattr(net, "make_client", lambda base, cfg: SessionClient(base))
    ctx = net.Ctx(home, Store(home), {})
    errors = []

    async def use():
        async with net.connected(ctx):
            await asyncio.sleep(0.3)

    def worker():
        try:
            net.run(use())
        except Exception as exc:  # noqa: BLE001
            errors.append(repr(exc))
    threads = [threading.Thread(target=worker) for _ in range(3)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(30)
    assert errors == []


def test_a_locked_session_reads_in_plain_words_and_is_logged(home):
    import sqlite3

    from tgkit import applog, setupflow
    try:
        raise sqlite3.OperationalError("database is locked")
    except sqlite3.OperationalError as exc:
        err = setupflow._friendly(exc, home)
    assert "OperationalError" not in err.message + err.hint and "busy" in err.message
    other = setupflow._friendly(KeyError("x"), home)
    assert "KeyError" not in other.message + other.hint and "Copy diagnostics" in other.hint
    log = applog.log_path(home).read_text(encoding="utf-8")
    assert "Traceback" in log and "database is locked" in log and "setup in the window" in log


def test_log_masks_phone_numbers(home):
    from tgkit import applog
    applog.caught("login", ValueError("bad +84 912 345 678"), home)
    text = applog.log_path(home).read_text(encoding="utf-8")
    assert "912 345" not in text and "+***" in text


def test_diagnostics_and_log_folder(home, server):
    from tgkit import applog, config
    config.put(home, api_id="1234567", api_hash="ab" * 16)
    applog.caught("test", RuntimeError("boom"), home)
    _, base = server
    code, body = call(base, "/api/diagnostics")
    text = json.loads(body)["text"]
    assert code == 200 and "TG Kit diagnostics" in text and "RuntimeError: boom" in text
    assert "ab" * 16 not in text  # never the secret code
    code, body = call(base, "/api/open-logs", {})
    assert code == 200 and json.loads(body)["folder"].endswith("logs")


def test_switch_account_shows_only_the_new_accounts_chats_and_back(home):
    from tgkit import account, config
    from tgkit.store import Store
    a_chats = {d.name for d in Store(home).chat_dirs()}
    assert a_chats
    res = account.adopt(home, {"id": 202, "name": "B", "username": None})
    assert res["switched"] and Store(home).chat_dirs() == []
    assert config.load(home)["me"]["id"] == 202 and account.owner(home)["id"] == 202
    assert {p.name for p in (home / "accounts" / "101" / "chats").iterdir()} >= a_chats  # A's chats aside
    (home / "chats" / "B_chat").mkdir(parents=True)  # B adds a chat of its own
    res = account.adopt(home, {"id": 101, "name": "An Nguyễn", "username": "an_test"})
    assert res["switched"] and res["restored"]
    assert {d.name for d in Store(home).chat_dirs()} == a_chats  # A's chats back, B's aside
    assert (home / "accounts" / "202" / "chats" / "B_chat").is_dir()
    assert account.adopt(home, {"id": 101, "name": "An Nguyễn", "username": "an_test"})["switched"] is False


def test_sync_refuses_another_accounts_login_without_touching_chats(home, monkeypatch):
    from tgkit import net
    from tgkit.store import Store
    monkeypatch.setattr(net, "make_client", lambda base, cfg: SessionClient(base, me_id=202))
    store = Store(home)
    before = {d.name: (d / "messages.jsonl").read_bytes() for d in store.chat_dirs()}
    with pytest.raises(Exception) as info:
        net.run(net.sync_chats(net.Ctx(home, store, {}), store.chat_dirs(), quiet=True))
    assert "another Telegram account" in str(info.value)
    assert {d.name: (d / "messages.jsonl").read_bytes() for d in store.chat_dirs()} == before


def test_a_login_that_fails_after_sign_in_frees_the_locks_and_saves_me(home, monkeypatch):
    """The stuck sync of 0.2.3: a failure after Telegram accepted the code left the
    login's sync lock held in the window, so no later sync or add could run."""
    from tgkit import config, net, setupflow
    from tgkit.store import SessionLock, SyncLock
    config.put(home, api_id="1234567", api_hash="0" * 32)

    class Client(SessionClient):
        async def send_code_request(self, phone):
            return type("Sent", (), {"phone_code_hash": "h"})()

        async def sign_in(self, **kw):
            if kw.get("code") == "99999":
                raise type("AuthRestartError", (Exception,), {})()

        async def is_user_authorized(self):
            return False
    monkeypatch.setattr(net, "make_client", lambda base, cfg: Client(base, me_id=303))
    tg = setupflow.TelegramSetup(home)
    assert tg.phone("+84912345678")["step"] == "code"
    with pytest.raises(Exception) as info:
        tg.code("99999")
    assert "AuthRestartError" not in str(info.value) and tg.step is None
    SyncLock(home).acquire().release()  # free again
    SessionLock(home).acquire().release()
    assert tg.phone("+84912345678")["step"] == "code"
    res = tg.code("12345")
    assert res["step"] == "done" and res["switched"]
    assert config.load(home)["me"]["id"] == 303  # who logged in is saved
    SyncLock(home).acquire().release()


def test_api_needs_the_token(server):
    _, base = server
    assert call(base, "/api/state", token=None)[0] == 403
    assert call(base, "/api/state", token="wrong")[0] == 403


def test_state_lists_chats_and_mode(server):
    _, base = server
    code, body = call(base, "/api/state")
    s = json.loads(body)
    assert code == 200 and s["claude"] is False
    titles = {c["title"] for c in s["chats"]}
    assert {"Team Beta", "Phòng Kế toán"} <= titles


def test_offline_search_saves_the_conversation(home, server, tg):
    _, base = server
    before = (home / "chats" / "Du_an_Alpha" / "messages.jsonl").read_bytes()
    evs = ask(base, "tìm biên bản")
    kinds = [e["type"] for e in evs]
    assert kinds[0] == "conversation" and kinds[-1] == "done"
    assert {"type": "mode", "mode": "offline"} in evs
    cards = [c for e in evs if e["type"] == "cards" for c in e["cards"]]
    assert cards and cards[0]["type"] == "hits" and cards[0]["total"] >= 2
    conv = evs[0]["id"]
    evs2 = ask(base, "việc của tôi", conv)
    assert evs2[0]["id"] == conv
    tasks = [c for e in evs2 if e["type"] == "cards" for c in e["cards"]]
    assert tasks[0]["type"] == "tasks"

    code, out, err = tg("chats-log", "--json")
    assert code == 0, err
    log = json.loads(out)["conversations"]
    assert log[0]["id"] == conv
    assert [t["text"] for t in log[0]["turns"] if t["role"] == "user"] == ["tìm biên bản", "việc của tôi"]
    assert log[0]["turns"][1]["intent"]["kind"] == "search"
    code, out, err = tg("chats-log", conv)
    assert code == 0 and "tìm biên bản" in out
    # The app never writes a chat folder.
    assert (home / "chats" / "Du_an_Alpha" / "messages.jsonl").read_bytes() == before


def test_task_buttons(home, server):
    _, base = server
    evs = ask(base, "all tasks")
    items = [c for e in evs if e["type"] == "cards" for c in e["cards"]][0]["items"]
    first = items[0]["id"]
    code, body = call(base, f"/api/tasks/{first}", {"status": "done"})
    assert code == 200 and json.loads(body)["task"]["status"] == "done"
    saved = json.loads((home / "tasks.json").read_text(encoding="utf-8"))
    assert [t["status"] for t in saved["tasks"] if t["id"] == first] == ["done"]
    assert refused(*call(base, f"/api/tasks/{first}", {"status": "nonsense"}))
    assert refused(*call(base, "/api/tasks/9999", {"status": "done"}))


def test_thread_and_archive(server):
    _, base = server
    code, body = call(base, "/api/thread?folder=Du_an_Alpha&id=21")
    items = json.loads(body)["items"]
    assert code == 200 and [m["id"] for m in items] == [20, 21]
    assert [m["anchor"] for m in items] == [False, True]
    assert refused(*call(base, "/api/thread?folder=..&id=1"))
    conv = ask(base, "summary")[0]["id"]
    assert call(base, f"/api/conversations/{conv}/archive", {})[0] == 200
    s = json.loads(call(base, "/api/state")[1])
    assert conv not in [c["id"] for c in s["conversations"]]
    assert call(base, "/api/conversations/..%2Fconfig")[0] in (400, 404)


def test_summary_and_help_answers(server):
    _, base = server
    evs = ask(base, "tuần này nhóm alpha bàn gì?")
    done = evs[-1]
    assert done["type"] == "done" and "tin nhắn" in done["text"]
    cards = [c for e in evs if e["type"] == "cards" for c in e["cards"]]
    assert cards[0]["type"] == "summary" and cards[0]["chats"][0]["folder"] == "Du_an_Alpha"
    evs = ask(base, "help")
    assert [c for e in evs if e["type"] == "cards" for c in e["cards"]][0]["type"] == "examples"


def test_unknown_chat_is_a_plain_error(home, server):
    _, base = server
    (home / "chats").rename(home / "chats-moved")
    evs = ask(base, "summary")
    err = [e for e in evs if e["type"] == "error"][0]
    assert "no chats" in err["message"] and err["needs"] == "setup"


def test_window_falls_back_without_pywebview(monkeypatch):
    monkeypatch.setitem(sys.modules, "webview", None)
    assert appmod._window("http://127.0.0.1:1/") is False
