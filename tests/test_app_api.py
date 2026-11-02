"""The app's local API, over real HTTP on 127.0.0.1. No network beyond that, no Claude."""

import json
import sys
import threading
import urllib.error
import urllib.request

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
    assert call(base, f"/api/tasks/{first}", {"status": "nonsense"})[0] == 400
    assert call(base, "/api/tasks/9999", {"status": "done"})[0] == 400


def test_thread_and_archive(server):
    _, base = server
    code, body = call(base, "/api/thread?folder=Du_an_Alpha&id=21")
    items = json.loads(body)["items"]
    assert code == 200 and [m["id"] for m in items] == [20, 21]
    assert [m["anchor"] for m in items] == [False, True]
    assert call(base, "/api/thread?folder=..&id=1")[0] == 400
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
