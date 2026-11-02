"""0.2.3: log out / switch account, Claude status, model and effort, token use,
Save as file (docx, xlsx, csv, md), Save file, and links to messages in Telegram.
No network and no real Claude: tests/fake_claude.py stands in for it."""

import csv
import io
import json
import sys
import threading
import zipfile
from pathlib import Path
from xml.etree import ElementTree

import pytest

from fixture_data import MINUTES_FILE, _row, write_chat
from test_app_api import TOKEN, call, refused
from test_claude_mode import permitted

from tgkit import account, claude, config, engine, export, usage
from tgkit.appserver import App, serve
from tgkit.claude import ClaudeRunner
from tgkit.errors import UserError
from tgkit.store import Store, SyncLock

STUB = str(Path(__file__).resolve().parent / "fake_claude.py")
HELP = """Usage: claude [options] [command] [prompt]
Options:
  --effort <level>                      Effort level for the current session
                                        (low, medium, high, xhigh, max)
  --model <model>                       Model for the current session. Provide
                                        an alias for the latest model (e.g.
                                        'fable', 'opus', or 'sonnet') or a
                                        model's full name.
  -p, --print                           Print response and exit
Commands:
  auth                                  Manage authentication
  doctor                                Check the health
"""
AUTH_OK = json.dumps({"loggedIn": True, "authMethod": "claude.ai", "email": "an@example.com",
                      "subscriptionType": "max"})


@pytest.fixture(autouse=True)
def fresh_help_cache():
    claude._HELP_CACHE.clear()
    yield
    claude._HELP_CACHE.clear()


def runner():
    return ClaudeRunner(cmd=[sys.executable, STUB])


def log_runs(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


# ------------------------------------------------------------------ log out


def test_logout_keeps_chats_by_default_and_removes_the_login(home, tg):
    (home / "tgkit.session").write_bytes(b"fake session")
    before = (home / "chats" / "Du_an_Alpha" / "messages.jsonl").read_bytes()
    calls = []
    res = account.logout(home, "keep", server=lambda base, cfg: calls.append(1) or True)
    assert not (home / "tgkit.session").exists()
    assert "me" not in config.load(home)
    assert (home / "chats" / "Du_an_Alpha" / "messages.jsonl").read_bytes() == before
    assert res["backup"] is None and res["was"] == "An Nguyễn"
    assert not calls  # no api_id saved: Telegram cannot be asked, the local log out still happens
    code, out, _ = tg("where")
    assert "Logged in on this computer: no" in out


def test_logout_asks_telegram_when_it_can(home, monkeypatch):
    config.save(home, {**config.load(home), "api_id": "1234567", "api_hash": "0" * 32})
    (home / "tgkit.session").write_bytes(b"x")
    seen = {}

    class FakeClient:
        async def connect(self):
            seen["connect"] = True

        async def is_user_authorized(self):
            return True

        async def log_out(self):
            seen["log_out"] = True
            return True

        def disconnect(self):
            seen["disconnect"] = True

    from tgkit import net
    monkeypatch.setattr(net, "make_client", lambda base, cfg: FakeClient())
    res = account.logout(home, "keep")
    assert res["telegram"] is True and seen == {"connect": True, "log_out": True, "disconnect": True}
    assert config.load(home)["api_id"] == "1234567"  # the codes stay: they work with any account


def test_logout_offline_still_logs_out_here(home):
    config.save(home, {**config.load(home), "api_id": "1234567", "api_hash": "0" * 32})
    (home / "tgkit.session").write_bytes(b"x")

    def offline(base, cfg):
        raise ConnectionError("no network")
    res = account.logout(home, "keep", server=offline)
    assert res["telegram"] is False and "could not be reached" in res["telegram_note"]
    assert not (home / "tgkit.session").exists()


def test_logout_backup_moves_and_never_deletes(home):
    (home / "tgkit.session").write_bytes(b"x")
    msgs = (home / "chats" / "Team_Beta" / "messages.jsonl").read_bytes()
    res = account.logout(home, "backup", server=lambda b, c: False)
    dest = Path(res["backup"])
    assert dest.parent == home and dest.name.startswith("old-account-")
    assert (dest / "chats" / "Team_Beta" / "messages.jsonl").read_bytes() == msgs
    assert (dest / "senders.json").exists() and (dest / "READ ME.txt").exists()
    assert not (home / "chats").exists()
    assert Store(home).chat_dirs() == []


def test_logout_backup_keeps_token_use_and_the_daily_stop(home):
    config.save(home, {**config.load(home), "usage_daily_limit": 1000, "usage_hard_stop": True,
                       "claude_model": "haiku"})
    usage.record(home, {"input": 5000, "output": 10})
    assert usage.limit_state(home, config.load(home))["reached"]
    res = account.logout(home, "backup", server=lambda b, c: False)
    assert not (Path(res["backup"]) / usage.FILE).exists()
    assert usage.totals(home)["today"]["tokens"] == 5010
    cfg = config.load(home)
    st = usage.limit_state(home, cfg)
    assert st["reached"] and st["hard"] and cfg["claude_model"] == "haiku"


def test_logout_waits_for_a_running_sync(home):
    (home / "tgkit.session").write_bytes(b"x")
    with SyncLock(home):
        with pytest.raises(UserError) as exc:
            account.logout(home, "keep", server=lambda b, c: True)
    assert "sync" in exc.value.message
    assert (home / "tgkit.session").exists()


def test_logout_command_confirms_first(home, tg, monkeypatch):
    (home / "tgkit.session").write_bytes(b"x")
    monkeypatch.setattr("builtins.input", lambda prompt="": "")
    code, out, _ = tg("logout")
    assert code == 0 and "Nothing was changed" in out and (home / "tgkit.session").exists()
    answers = iter(["yes", "2"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    code, out, _ = tg("logout")
    assert code == 0 and "moved to" in out and "tgkit setup" in out
    assert not (home / "tgkit.session").exists() and not (home / "chats").exists()
    code, out, _ = tg("logout")
    assert code == 0 and "nothing to log out" in out


def test_menu_has_log_out(home):
    from tgkit import wizard
    ran = []
    answers = iter(["8", "", "0"])
    wizard.run_menu(lambda argv: ran.append(argv), ask=lambda p: next(answers), say=lambda s: None)
    assert ran == [["logout"]]
    assert "Log out / switch Telegram account" in wizard.MENU


# ------------------------------------------------------------------ Claude status


def test_claude_status_states(home, monkeypatch):
    cmd = [sys.executable, STUB]
    assert claude.status([])["state"] == "missing"
    monkeypatch.setenv("FAKE_CLAUDE_HELP", HELP)
    monkeypatch.setenv("FAKE_CLAUDE_AUTH", AUTH_OK)
    st = claude.status(cmd, home)
    assert st["state"] == "connected" and st["account"] == "an@example.com" and st["plan"] == "max"
    assert st["version"] == "9.9.9" and st["efforts"] == ["low", "medium", "high", "xhigh", "max"]
    assert st["models"][:3] == ["sonnet", "opus", "haiku"] and "fable" in st["models"]
    claude._HELP_CACHE.clear()
    monkeypatch.setenv("FAKE_CLAUDE_AUTH", json.dumps({"loggedIn": False}))
    st = claude.status(cmd, home)
    assert st["state"] == "not_logged_in" and any("claude auth login" in s for s in st["fix"])
    claude._HELP_CACHE.clear()
    monkeypatch.delenv("FAKE_CLAUDE_HELP")
    st = claude.status(cmd, home)
    assert st["state"] == "unknown" and st["effort"] == "" and st["efforts"] == []


def test_claude_status_command(home, tg, monkeypatch):
    monkeypatch.setenv("TGKIT_CLAUDE", STUB)
    monkeypatch.setenv("FAKE_CLAUDE_HELP", HELP)
    monkeypatch.setenv("FAKE_CLAUDE_AUTH", AUTH_OK)
    code, out, _ = tg("claude-status", "--json")
    assert code == 0 and json.loads(out)["state"] == "connected"
    code, out, _ = tg("claude-status")
    assert "installed and logged in" in out and "an@example.com" in out
    monkeypatch.setenv("TGKIT_CLAUDE", "off")
    code, out, _ = tg("claude-status")
    assert "not installed" in out and "code.claude.com" in out


# ------------------------------------------------------------------ model, effort, usage


def test_model_and_effort_reach_every_claude_call(home, tmp_path, monkeypatch):
    log = tmp_path / "claude.log"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    monkeypatch.setenv("FAKE_CLAUDE_HELP", HELP)
    config.save(home, {**config.load(home), "claude_model": "opus", "claude_effort": "high"})
    app = App(home, claude=runner(), token="t")
    evs = list(app.ask({"text": "tìm biên bản"}))
    argv = log_runs(log)[0]["argv"]
    assert argv[argv.index("--model") + 1] == "opus" and argv[argv.index("--effort") + 1] == "high"
    turn = evs[-1]["turn"]
    assert turn["model"] == "opus"
    assert turn["usage"] == {"input": 120, "output": 45, "cache_read": 3000, "cache_write": 200}
    # A Claude Code without --effort never gets it: detected, not guessed.
    claude._HELP_CACHE.clear()
    monkeypatch.delenv("FAKE_CLAUDE_HELP")
    list(app.ask({"text": "tìm biên bản"}))
    argv = log_runs(log)[1]["argv"]
    assert "--effort" not in argv and argv[argv.index("--model") + 1] == "opus"


def test_default_model_passes_no_model_flag(home, tmp_path, monkeypatch):
    log = tmp_path / "claude.log"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    list(App(home, claude=runner(), token="t").ask({"text": "tìm biên bản"}))
    argv = log_runs(log)[0]["argv"]
    assert "--model" not in argv and "--effort" not in argv


def test_usage_is_saved_and_totalled(home, tg, monkeypatch):
    app = App(home, claude=runner(), token="t")
    evs = list(app.ask({"text": "tìm biên bản"}))
    conv = evs[0]["id"]
    monkeypatch.setenv("FAKE_CLAUDE_COST", "0.0123")
    list(app.ask({"text": "còn gì nữa", "conversation": conv}))
    list(app.ask({"text": "summary", "mode": "offline"}))  # offline: no tokens, no line
    lines = usage.entries(home)
    assert len(lines) == 2 and lines[0]["conversation"] == conv and "cost_usd" not in lines[0]
    assert lines[1]["cost_usd"] == 0.0123
    tot = app.usage(conv)
    assert tot["today"]["answers"] == 2 and tot["today"]["tokens"] == 2 * (120 + 45 + 3000 + 200)
    assert tot["conversation"]["answers"] == 2 and tot["month"]["cost_usd"] == 0.0123
    code, out, _ = tg("usage", "--json")
    data = json.loads(out)
    assert code == 0 and data["today"]["input"] == 240 and "use none" in data["what_uses_tokens"]
    code, out, _ = tg("usage")
    assert "Claude Code's estimate" in out and "use none" in out


def test_daily_limit_warns_or_stops(home, tmp_path, monkeypatch):
    log = tmp_path / "claude.log"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    app = App(home, claude=runner(), token="t")
    list(app.ask({"text": "tìm biên bản"}))
    config.save(home, {**config.load(home), "usage_daily_limit": 100})
    evs = list(app.ask({"text": "tìm biên bản"}))
    assert any(e["type"] == "notice" and e["reason"] == "limit_soft" for e in evs)
    assert evs[-1]["mode"] == "claude" and len(log_runs(log)) == 2
    config.save(home, {**config.load(home), "usage_hard_stop": True})
    evs = list(app.ask({"text": "tìm biên bản"}))
    assert any(e["type"] == "notice" and e["reason"] == "limit" for e in evs)
    assert evs[-1]["mode"] == "offline" and len(log_runs(log)) == 2  # Claude was not started


def test_usage_reader_skips_a_damaged_line(home):
    usage.record(home, {"input": 1, "output": 2})
    with usage.path(home).open("a", encoding="utf-8") as fh:
        fh.write("{not json\n")
    usage.record(home, {"input": 3, "output": 4})
    assert usage.totals(home)["today"]["tokens"] == 10


# ------------------------------------------------------------------ settings API


@pytest.fixture
def server(home, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_HELP", HELP)
    monkeypatch.setenv("FAKE_CLAUDE_AUTH", AUTH_OK)
    app = App(home, claude=runner(), token=TOKEN)
    httpd = serve(app)
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    yield app, f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def test_settings_api_checks_and_saves(home, server):
    _, base = server
    for path in ("/api/settings", "/api/usage", "/api/claude-status"):
        assert call(base, path, token=None)[0] == 403
    code, body = call(base, "/api/settings")
    s = json.loads(body)
    assert code == 200 and s["model"] == "" and "opus" in s["models"] and "high" in s["efforts"]
    assert s["account"]["name"] == "An Nguyễn"
    code, body = call(base, "/api/settings", {"model": "gpt-9"})
    assert refused(code, body) and "not a model" in json.loads(body)["message"]
    code, body = call(base, "/api/settings", {"effort": "turbo"})
    assert refused(code, body)
    code, body = call(base, "/api/settings", {"usage_daily_limit": -5})
    assert refused(code, body)
    code, body = call(base, "/api/settings", {"export_dir": "relative/folder"})
    assert refused(code, body)
    code, body = call(base, "/api/settings", {"model": "haiku", "effort": "low", "usage_daily_limit": 50000,
                                              "usage_hard_stop": True})
    assert code == 200
    cfg = config.load(home)
    assert (cfg["claude_model"], cfg["claude_effort"], cfg["usage_daily_limit"], cfg["usage_hard_stop"]) == \
        ("haiku", "low", 50000, True)
    assert cfg["me"]["name"] == "An Nguyễn"  # other keys untouched
    code, body = call(base, "/api/claude-status?refresh=1")
    st = json.loads(body)
    assert st["state"] == "connected" and st["model"] == "haiku"


def test_logout_api(home, server):
    (home / "tgkit.session").write_bytes(b"x")
    _, base = server
    code, body = call(base, "/api/logout", {"data": "keep"})
    res = json.loads(body)
    assert code == 200 and res["ok"] and not (home / "tgkit.session").exists()
    assert (home / "chats").exists()
    assert json.loads(call(base, "/api/settings")[1])["account"]["logged_in"] is False


# ------------------------------------------------------------------ export


def docx_text(path: Path) -> str:
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            if name.endswith(".xml") or name.endswith(".rels"):
                ElementTree.fromstring(z.read(name))  # every part is well-formed XML
        xml = z.read("word/document.xml").decode("utf-8")
    return xml


def test_export_search_as_word_keeps_vietnamese_and_links(home, tg, tmp_path):
    code, out, err = tg("export", "search", "biên bản", "--json")
    assert code == 0, err
    saved = json.loads(out)
    path = Path(saved["path"])
    assert path.parent == tmp_path / "exports" and path.suffix == ".docx"
    xml = docx_text(path)
    assert "Biên bản họp đây ạ" in xml and MINUTES_FILE in xml
    with zipfile.ZipFile(path) as z:
        rels = z.read("word/_rels/document.xml.rels").decode("utf-8")
    assert "https://t.me/c/1001/24" in rels and 'TargetMode="External"' in rels
    docx = pytest.importorskip("docx")
    d = docx.Document(str(path))
    cells = [c.text for t in d.tables for r in t.rows for c in r.cells]
    assert "Biên bản họp đây ạ" in cells
    # a second export never overwrites the first
    code, out, _ = tg("export", "search", "biên bản", "--json")
    second = Path(json.loads(out)["path"])
    assert second != path and second.name.endswith("(2).docx") and path.exists()


def test_export_tasks_as_excel_csv_md_without_changing_tasks(home, tg, tmp_path):
    assert tg("tasks")[0] == 0
    before = (home / "tasks.json").read_bytes()
    paths = {}
    for fmt in ("xlsx", "csv", "md"):
        code, out, err = tg("export", "tasks", "all", "--format", fmt, "--json", "--lang", "vi")
        assert code == 0, err
        paths[fmt] = Path(json.loads(out)["path"])
    assert (home / "tasks.json").read_bytes() == before
    raw = paths["csv"].read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    rows = list(csv.reader(io.StringIO(raw.decode("utf-8-sig"))))
    assert rows[0][1] == "Việc" and len(rows) > 1
    assert any("biên bản" in r[1] for r in rows[1:])
    assert "| Số | Việc |" in paths["md"].read_text(encoding="utf-8")
    openpyxl = pytest.importorskip("openpyxl")
    ws = openpyxl.load_workbook(paths["xlsx"]).active
    assert ws.cell(1, 2).value == "Việc" and ws.max_row == len(rows)


def test_export_summary_and_conversation(home, tg):
    code, out, err = tg("export", "summary", "--since", "30d", "--json")
    assert code == 0, err
    xml = docx_text(Path(json.loads(out)["path"]))
    assert "Dự án Alpha: Kế hoạch" in xml and "Chốt phương án B" in xml
    app = App(home, claude=ClaudeRunner(cmd=[]), token="t")
    conv = list(app.ask({"text": "tìm biên bản"}))[0]["id"]
    res = app.export({"kind": "conversation", "conversation": conv, "format": "docx"})
    xml = docx_text(Path(res["path"]))
    assert "tìm biên bản" in xml and "Biên bản họp đây ạ" in xml


def test_card_export_runs_the_search_again_for_every_result(home):
    app = App(home, claude=ClaudeRunner(cmd=[]), token="t")
    evs = list(app.ask({"text": "tìm biên bản"}))
    card = [c for e in evs if e["type"] == "cards" for c in e["cards"]][0]
    assert card["source"] == {"kind": "search", "query": "biên bản", "chat": None, "since": None}
    card["items"] = card["items"][:1]  # what the card showed is not the limit
    res = app.export({"kind": "card", "card": card, "format": "csv"})
    rows = list(csv.reader(io.StringIO(Path(res["path"]).read_text(encoding="utf-8-sig"))))
    assert len(rows) - 1 == card["total"] >= 2
    # a card without a source is saved as it is
    res = app.export({"kind": "card", "card": {"type": "hits", "query": "x", "items": card["items"]},
                      "format": "md"})
    assert "Biên bản" in Path(res["path"]).read_text(encoding="utf-8")


def test_claude_cards_know_their_command():
    assert engine.source_of('tgkit search "biên bản" --chat "ke toan" --since 7d --json') == \
        {"kind": "search", "query": "biên bản", "chat": "ke toan", "since": "7d"}
    assert engine.source_of("tgkit tasks mine --since 30d --json")["which"] == "mine"
    assert engine.source_of('tgkit summary "Team" --json')["chat"] == "Team"
    assert engine.source_of("tgkit show x") is None and engine.source_of('tgkit search "x') is None


def test_save_attachment_copies_only_local_files(home, tg, tmp_path):
    code, out, err = tg("export", "file", "alpha", "24", "--json")
    assert code == 0, err
    copied = Path(json.loads(out)["path"])
    assert copied.name == MINUTES_FILE and copied.read_bytes() == b"%PDF-1.4 fake\n"
    code, out, err = tg("export", "file", "ke toan", "70")
    assert code == 1 and "not on this computer" in err
    with pytest.raises(UserError):
        export.save_attachment(home, "../outside", 24)
    # a media_path that points outside the chat's media folder is refused
    d = home / "chats" / "Team_Beta"
    rows = [json.loads(x) for x in (d / "messages.jsonl").read_text(encoding="utf-8").splitlines()]
    rows[0]["media"], rows[0]["media_path"] = {"file_name": "c.json"}, "../config.json"
    (d / "messages.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                                      encoding="utf-8")
    with pytest.raises(UserError) as exc:
        export.save_attachment(home, "Team_Beta", rows[0]["id"])
    assert "not on this computer" in exc.value.message


def test_safe_names():
    assert export.safe_name('Tìm kiếm: "biên bản" 2026-11-02') == "Tìm kiếm biên bản 2026-11-02"
    assert export.safe_name("CON") == "TG Kit CON"
    assert export.safe_name("a/b\\c?.") == "a b c"


# ------------------------------------------------------------------ links to messages


def add_private_chats(home):
    """Two private chats: one person with a username, one without."""
    write_chat(home, "Binh_rieng", {"id": 5005, "type": "User", "title": None, "username": "binh_test",
                                    "first_name": "Bình", "last_name": "Riêng"},
               [_row(90, 30, 102, "Gửi em biên bản riêng nhé")])
    write_chat(home, "Chi_rieng", {"id": 6006, "type": "User", "title": None, "username": None,
                                   "first_name": "Chi", "last_name": "Riêng"},
               [_row(91, 29, 103, "Cần gửi biên bản cho chị trước 17h hôm nay")])


def test_message_links(home, tg_json):
    store = Store(home)
    assert store.message_link(store.chats / "Du_an_Alpha", 24) == "https://t.me/c/1001/24"
    assert store.message_link(store.chats / "Phòng_Kế_toán", 70) == "https://t.me/ketoan_test/70"
    res = tg_json("search", "biên bản")["results"]
    assert {r["id"]: r["link"] for r in res}[24] == "https://t.me/c/1001/24"
    chats = {c["folder"]: c for c in engine.chat_list(store)}
    assert chats["Phòng_Kế_toán"]["link_base"] == "https://t.me/ketoan_test" and chats["Team_Beta"]["link_base"] is None


def test_links_per_chat_type(home, tg_json):
    """Supergroup or channel: the message. Private chat and basic group: the chat,
    said plainly (Telegram has no message links for them)."""
    add_private_chats(home)
    store = Store(home)

    def info(folder, ident):
        return store.link_info(store.chats / folder, ident)
    assert info("Du_an_Alpha", 24) == {"link": "https://t.me/c/1001/24", "link_kind": "message"}
    assert info("Phòng_Kế_toán", 70) == {"link": "https://t.me/ketoan_test/70", "link_kind": "message"}
    assert info("Binh_rieng", 90) == {"link": "tg://resolve?domain=binh_test", "link_kind": "chat"}
    assert info("Chi_rieng", 91) == {"link": "tg://user?id=6006", "link_kind": "chat"}
    assert info("Team_Beta", 50) == {"link": "tg://openmessage?chat_id=2002&message_id=50", "link_kind": "chat"}
    # search hits: every row has a link and says what it opens
    hits = {r["folder"]: r for r in tg_json("search", "biên bản")["results"]}
    assert hits["Du_an_Alpha"]["link_kind"] == "message"
    assert hits["Binh_rieng"]["link"] == "tg://resolve?domain=binh_test" and hits["Binh_rieng"]["link_kind"] == "chat"
    assert hits["Chi_rieng"]["link"] == "tg://user?id=6006"
    # the chat list carries one template per chat for tasks and summaries in the window
    tpl = {c["folder"]: (c["link_tpl"], c["link_kind"]) for c in engine.chat_list(store)}
    assert tpl["Du_an_Alpha"] == ("https://t.me/c/1001/{id}", "message")
    assert tpl["Team_Beta"] == ("tg://openmessage?chat_id=2002&message_id={id}", "chat")
    assert tpl["Binh_rieng"] == ("tg://resolve?domain=binh_test", "chat")
    assert tpl["Empty_Chat"] == ("tg://user?id=4004", "chat")


def test_exports_link_every_chat_type(home, tg):
    add_private_chats(home)
    code, out, err = tg("export", "search", "biên bản", "--format", "md", "--json")
    assert code == 0, err
    md = Path(json.loads(out)["path"]).read_text(encoding="utf-8")
    assert "[Open in Telegram](https://t.me/c/1001/24)" in md
    assert "[Open the chat in Telegram](tg://resolve?domain=binh_test)" in md
    assert "[Open the chat in Telegram](tg://user?id=6006)" in md
    assert tg("tasks", "--since", "3650d")[0] == 0
    code, out, err = tg("export", "tasks", "all", "--format", "csv", "--json")
    assert code == 0, err
    rows = list(csv.reader(io.StringIO(Path(json.loads(out)["path"]).read_text(encoding="utf-8-sig"))))
    links = {r[5]: r[-1] for r in rows[1:]}  # chat -> link
    assert links["Team Beta"].startswith("tg://openmessage?chat_id=2002&message_id=")
    assert links["Chi Riêng"] == "tg://user?id=6006"
    assert all(r[-1] for r in rows[1:])  # no task without a link
    code, out, err = tg("export", "summary", "--since", "30d", "--format", "md", "--json", "--lang", "vi")
    assert code == 0, err
    md = Path(json.loads(out)["path"]).read_text(encoding="utf-8")
    assert "[Mở cuộc trò chuyện trên Telegram](tg://openmessage?chat_id=2002&message_id=" in md
    assert "[Mở trên Telegram](https://t.me/c/1001/" in md
    code, out, err = tg("export", "search", "biên bản", "--json")  # Word: a real hyperlink
    with zipfile.ZipFile(Path(json.loads(out)["path"])) as z:
        rels = z.read("word/_rels/document.xml.rels").decode("utf-8")
    assert "tg://user?id=6006" in rels


def test_open_link_api_only_opens_links_of_chats_in_tg_kit(home, server):
    add_private_chats(home)
    _, base = server

    def opened(url, lang="en"):
        code, body = call(base, "/api/open-link", {"url": url}) if lang == "en" else \
            call_lang(base, "/api/open-link", {"url": url}, lang)
        assert code == 200, body
        return json.loads(body)
    r = opened("https://t.me/c/1001/24")
    assert r["ok"] and r["opens"] == "message" and r["note"] is None
    assert opened("https://t.me/ketoan_test/70")["opens"] == "message"
    assert opened("tg://privatepost?channel=1001&post=24")["url"] == "tg://privatepost?channel=1001&post=24"
    r = opened("tg://resolve?domain=binh_test")
    assert r["opens"] == "chat" and r["url"] == "tg://resolve?domain=binh_test" and r["note"] is None
    r = opened("tg://user?id=6006")
    assert r["opens"] == "chat" and "Chi Riêng" in r["note"]  # Telegram Desktop cannot jump there: said plainly
    r = opened("tg://openmessage?chat_id=2002&message_id=50")
    assert r["opens"] == "chat" and r["url"] == "tg://openmessage?chat_id=2002&message_id=50"
    assert "Team Beta" in opened("tg://openmessage?chat_id=2002", "vi")["note"]
    assert "Telegram Desktop không tự mở" in opened("tg://user?id=6006", "vi")["note"]
    for bad in ("https://evil.example/x", "file:///C:/Windows", "https://t.me/c/1001/24 & calc", "tg://x",
                "tg://user?id=999", "tg://user?id=2002", "tg://openmessage?chat_id=1001",
                "tg://resolve?domain=somebody_else", "tg://resolve?domain=binh_test&post=5",
                "https://t.me/c/2002/50", "https://t.me/binh_test/90", "tg://privatepost?channel=2002&post=1",
                "tg://user?id=6006&x=calc", "TG://user?id=6006", "tg://resolve?domain=binh_test#x",
                "tg://resolve?domain=binh_test\n", "javascript:alert(1)", ""):
        assert refused(*call(base, "/api/open-link", {"url": bad})), bad


def call_lang(base, path, body, lang):
    import urllib.request  # noqa: PLC0415

    req = urllib.request.Request(base + path, data=json.dumps(body).encode("utf-8"), method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-TGKit-Token", TOKEN)
    req.add_header("X-TGKit-Lang", lang)
    with urllib.request.urlopen(req, timeout=20) as res:
        return res.status, res.read().decode("utf-8")


def test_reveal_api_stays_in_the_export_folder(home, server, tmp_path):
    _, base = server
    code, body = call(base, "/api/export", {"kind": "tasks", "format": "md"})
    path = json.loads(body)["path"]
    assert code == 200 and call(base, "/api/reveal", {"path": path})[0] == 200
    assert refused(*call(base, "/api/reveal", {"path": str(home / "config.json")}))


# ------------------------------------------------------------------ the read-only Claude session


def test_claude_session_may_read_usage_but_not_export_or_log_out(home, tg, monkeypatch):
    for shell in ("Bash", "PowerShell"):
        for cmd in ("tgkit usage", "tgkit usage --json", "tgkit claude-status --json"):
            assert permitted(cmd, shell), (shell, cmd)
        for cmd in ('tgkit export search "x"', "tgkit export tasks --format xlsx", "tgkit logout",
                    "tgkit logout --yes", "tgkit login"):
            assert not permitted(cmd, shell), (shell, cmd)
    (home / "tgkit.session").write_bytes(b"x")
    monkeypatch.setenv("TGKIT_READONLY", "1")
    for argv in (["export", "search", "biên bản"], ["logout", "--yes"], ["export", "file", "alpha", "24"]):
        code, out, err = tg(*argv)
        assert code == 1 and "read-only" in err, argv
    assert (home / "tgkit.session").exists()
    assert tg("usage", "--json")[0] == 0


# ------------------------------------------------------------------ setup in the window


class SessionPasswordNeededError(Exception):
    pass


class PhoneCodeInvalidError(Exception):
    pass


class LoginClient:
    """Telethon's login calls, no network. Wrong code 11111, two-step password 'pw'."""

    def __init__(self, two_step=True):
        self.two_step = two_step
        self.calls = []
        self.authorized = False

    async def connect(self):
        self.calls.append("connect")

    async def is_user_authorized(self):
        return self.authorized

    async def send_code_request(self, phone):
        self.calls.append(("send", phone))
        return type("Sent", (), {"phone_code_hash": "h1"})()

    async def sign_in(self, phone=None, code=None, phone_code_hash=None, password=None):
        if password is not None:
            if password != "pw":
                raise type("PasswordHashInvalidError", (Exception,), {})()
            self.authorized = True
            return None
        assert phone_code_hash == "h1"
        if code == "11111":
            raise PhoneCodeInvalidError()
        if self.two_step:
            raise SessionPasswordNeededError()
        self.authorized = True

    async def get_me(self):
        return type("User", (), {"id": 77, "first_name": "Bình", "last_name": "Trần", "username": "binh"})()

    async def disconnect(self):
        self.calls.append("disconnect")


def lock_free(home) -> bool:
    try:
        SyncLock(home).acquire().release()
        return True
    except UserError:
        return False


def test_setup_codes_are_checked_and_saved(home, server):
    _, base = server
    assert call(base, "/api/setup/codes", {"api_id": "12", "api_hash": "x"}, token=None)[0] == 403
    code, body = call(base, "/api/setup/codes", {"api_id": "abc", "api_hash": "0" * 32})
    assert refused(code, body) and "api_id" in json.loads(body)["message"]
    code, body = call(base, "/api/setup/codes", {"api_id": "1234567", "api_hash": "1" * 31})
    assert refused(code, body) and "api_hash" in json.loads(body)["message"]
    code, body = call(base, "/api/setup/codes", {"api_id": " 1234567 ", "api_hash": "ab" * 16})
    st = json.loads(body)
    assert code == 200 and st["has_codes"] and st["api_id"] == "1234567"
    cfg = config.load(home)
    assert cfg["api_hash"] == "ab" * 16 and cfg["me"]["name"] == "An Nguyễn"  # the rest stays
    assert "api_hash" not in st  # the secret half is never sent back to the page


def test_setup_login_with_two_step_password(home, server, monkeypatch):
    from tgkit import net
    config.save(home, {**config.load(home), "api_id": "1234567", "api_hash": "0" * 32})
    client = LoginClient()
    monkeypatch.setattr(net, "make_client", lambda base, cfg: client)
    _, base = server
    code, body = call(base, "/api/setup/phone", {"phone": "+84 912 345 678"})
    assert code == 200 and json.loads(body)["step"] == "code"
    assert ("send", "+84912345678") in client.calls
    assert not lock_free(home)  # no sync touches the login file during the login
    code, body = call(base, "/api/setup/code", {"code": "11111"})
    assert refused(code, body) and "not right" in json.loads(body)["message"]
    assert json.loads(call(base, "/api/setup/state")[1])["step"] == "code"  # can type it again
    code, body = call(base, "/api/setup/code", {"code": "2 2 2 2 2"})
    assert code == 200 and json.loads(body)["step"] == "password"
    code, body = call(base, "/api/setup/password", {"password": "nope"})
    assert refused(code, body) and "password" in json.loads(body)["message"]
    code, body = call(base, "/api/setup/password", {"password": "pw"})
    res = json.loads(body)
    assert code == 200 and res["step"] == "done" and res["me"]["name"] == "Bình Trần"
    assert config.load(home)["me"] == {"id": 77, "name": "Bình Trần", "username": "binh"}
    assert lock_free(home) and client.calls[-1] == "disconnect"
    assert "pw" not in (home / "config.json").read_text(encoding="utf-8")


def test_setup_cancel_frees_the_sync_lock(home, server, monkeypatch):
    from tgkit import net
    config.save(home, {**config.load(home), "api_id": "1234567", "api_hash": "0" * 32})
    monkeypatch.setattr(net, "make_client", lambda base, cfg: LoginClient(two_step=False))
    _, base = server
    assert refused(*call(base, "/api/setup/phone", {"phone": "123"}))  # too short: nothing started
    assert lock_free(home)
    assert call(base, "/api/setup/phone", {"phone": "+84912345678"})[0] == 200
    assert not lock_free(home)
    assert json.loads(call(base, "/api/setup/cancel", {})[1])["step"] is None
    assert lock_free(home)
    code, body = call(base, "/api/setup/code", {"code": "22222"})
    assert refused(code, body) and "Send code" in json.loads(body)["hint"]


def test_setup_refuses_while_a_sync_holds_the_lock(home, server, monkeypatch):
    from tgkit import net
    config.save(home, {**config.load(home), "api_id": "1234567", "api_hash": "0" * 32})
    monkeypatch.setattr(net, "make_client", lambda base, cfg: LoginClient())
    _, base = server
    with SyncLock(home):
        code, body = call(base, "/api/setup/phone", {"phone": "+84912345678"})
    assert refused(code, body) and "sync" in json.loads(body)["message"]
    assert json.loads(call(base, "/api/setup/state")[1])["step"] is None


def test_setup_pick_chats(home, server, monkeypatch):
    from test_sync_guard import Channel

    from tgkit import net
    config.save(home, {**config.load(home), "api_id": "1234567", "api_hash": "0" * 32})
    found = [{"name": "Du an Alpha", "kind": "group", "id": 1001, "username": None, "entity": Channel("a", 1001)},
             {"name": "Kế toán", "kind": "group", "id": 2002, "username": None, "entity": Channel("b", 2002)}]

    class Client(LoginClient):
        async def is_user_authorized(self):
            return True

    async def fake_list(client, limit=300):
        return [dict(d) for d in found]
    chosen = {}

    async def fake_add(ctx, query, limit, pick):
        dialogs = [dict(d, added=(d["id"] == 1001)) for d in found]
        chosen["names"] = [d["name"] for d in pick(dialogs, query)]
        chosen["limit"] = limit
        return 0
    monkeypatch.setattr(net, "make_client", lambda base, cfg: Client())
    monkeypatch.setattr(net, "list_dialogs", fake_list)
    monkeypatch.setattr(net, "add_chats", fake_add)
    _, base = server
    code, body = call(base, "/api/setup/dialogs")
    ds = {d["name"]: d for d in json.loads(body)["dialogs"]}
    assert code == 200 and ds["Kế toán"]["added"] is False and ds["Du an Alpha"]["added"] is True
    assert "entity" not in ds["Kế toán"]
    assert refused(*call(base, "/api/setup/add", {"ids": []}))
    code, body = call(base, "/api/setup/add", {"ids": ["1001", "2002"], "limit": 1000})
    assert code == 200 and chosen == {"names": ["Kế toán"], "limit": 1000}  # an added chat is never re-added
    call(base, "/api/setup/add", {"ids": ["2002"], "limit": "all"})
    assert chosen["limit"] is None


def test_settings_file_size_and_data_folder(home, server):
    _, base = server
    s = json.loads(call(base, "/api/settings")[1])
    assert s["max_media_mb"] == 50 and s["home"] == str(home)
    assert refused(*call(base, "/api/settings", {"max_media_mb": 0}))
    assert refused(*call(base, "/api/settings", {"max_media_mb": "lots"}))
    assert json.loads(call(base, "/api/settings", {"max_media_mb": 200})[1])["max_media_mb"] == 200
    assert config.max_media_bytes(home, config.load(home)) == 200 * 1024 * 1024
    assert json.loads(call(base, "/api/open-data", {})[1])["folder"] == str(home)
    assert json.loads(call(base, "/api/setup/open-site", {})[1])["url"] == "https://my.telegram.org/apps"


# ------------------------------------------------------------------ 0.2.3 gate: config lock, language, setup folder


PUTTER = """
import sys
sys.path.insert(0, sys.argv[2])
from tgkit import config
for i in range(25):
    config.put(sys.argv[1], other_process=i)
"""


def test_every_config_write_keeps_the_others(home, tmp_path):
    """A settings change and a login finishing at the same moment both stay
    (gate note 1): one lock, across threads and processes, and an atomic write."""
    import subprocess

    import tgkit
    src = Path(tgkit.__file__).resolve().parent
    for p in src.glob("*.py"):  # every writer goes through config.update / config.put
        if p.name != "config.py":
            assert "config.save(" not in p.read_text(encoding="utf-8"), p.name
    app = App(home, claude=ClaudeRunner(cmd=[]), token="t")
    script = tmp_path / "putter.py"
    script.write_text(PUTTER, encoding="utf-8")
    proc = subprocess.Popen([sys.executable, str(script), str(home), str(src.parent)])

    def settings(n):
        for i in range(25):
            app.save_settings({"max_media_mb": 100 + i, "usage_daily_limit": 1000 + i})

    def login(n):
        for i in range(25):
            config.put(home, me={"id": 77, "name": f"Bình {i}", "username": "binh"})

    def codes(n):
        for i in range(25):
            app.tg.codes("1234567", "%032x" % i)
    threads = [threading.Thread(target=f, args=(0,)) for f in (settings, login, codes)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert proc.wait(60) == 0
    cfg = config.load(home)
    assert (cfg["max_media_mb"], cfg["usage_daily_limit"]) == (124, 1024)
    assert cfg["me"]["name"] == "Bình 24" and cfg["api_hash"] == "%032x" % 24
    assert cfg["other_process"] == 24
    assert not (home / "config.json.tmp").exists()


def test_a_refused_change_writes_nothing(home, server):
    _, base = server
    before = (home / "config.json").read_bytes()
    assert refused(*call(base, "/api/settings", {"usage_daily_limit": 5, "max_media_mb": 0}))
    assert (home / "config.json").read_bytes() == before  # the good key of a refused change is not saved either


def test_wrong_login_code_is_a_handled_answer_in_the_window_language(home, server, monkeypatch):
    """Gate notes 2 and 3: no HTTP error for the browser to log, and Vietnamese
    text in the Vietnamese window."""
    from tgkit import net
    config.save(home, {**config.load(home), "api_id": "1234567", "api_hash": "0" * 32})
    monkeypatch.setattr(net, "make_client", lambda base, cfg: LoginClient())
    _, base = server
    assert call(base, "/api/setup/phone", {"phone": "+84912345678"})[0] == 200
    code, body = call_lang(base, "/api/setup/code", {"code": "11111"}, "vi")
    res = json.loads(body)
    assert refused(code, body) and res["message"] == "Mã đăng nhập này chưa đúng."
    assert res["hint"] == "Xem lại mã trong ứng dụng Telegram rồi gõ lại."
    code, body = call_lang(base, "/api/setup/code", {"code": "11111"}, "en")
    assert json.loads(body)["message"] == "That login code was not right."
    code, body = call_lang(base, "/api/settings", {"model": "gpt-9"}, "vi")
    assert json.loads(body)["message"] == "'gpt-9' không phải mô hình TG Kit chọn được."
    assert call(base, "/api/setup/cancel", {})[0] == 200


def test_every_error_the_window_can_show_has_vietnamese():
    """Each UserError text of the modules the app's server runs, with its hint,
    has a Vietnamese text (a name or number inside is filled with X here)."""
    import ast

    import tgkit
    from tgkit.cli import _FRIENDLY
    from tgkit.errors import localize
    src = Path(tgkit.__file__).resolve().parent

    def sample(arg):
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            return arg.value
        if isinstance(arg, ast.JoinedStr):
            return "".join(v.value if isinstance(v, ast.Constant) else "X" for v in arg.values)
        return None
    texts = []
    for mod in ("account", "appserver", "config", "conversations", "engine", "export", "net", "setupflow",
                "store", "tasks"):
        tree = ast.parse((src / f"{mod}.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "UserError":
                texts += [(mod, sample(a)) for a in node.args]
            if isinstance(node, ast.FunctionDef) and node.name == "_friendly":  # setupflow's own hints
                texts += [(mod, n.value) for n in ast.walk(node)
                          if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value.endswith(".")
                          and " " in n.value]
    texts += [("cli._FRIENDLY", t) for pair in _FRIENDLY.values() for t in pair]
    assert len(texts) > 100
    missing = [f"{mod}: {t}" for mod, t in texts if t and localize(t, "vi") == t]
    assert not missing, "\n".join(missing)


def test_refused_post_reads_the_body_first(server):
    """Gate note 4: a 403 answered before the body was read reset the connection
    now and then (WinError 10053). The body is read first, so it never does."""
    _, base = server
    big = {"api_id": "1", "api_hash": "x" * 300_000}
    for _ in range(10):
        assert call(base, "/api/setup/codes", big, token=None)[0] == 403
        assert call(base, "/api/setup/codes", {"api_id": "12", "api_hash": "x"}, token=None)[0] == 403


@pytest.fixture
def fresh_server(tmp_path, monkeypatch):
    """A first start: no data yet, the data folder not fixed by TGKIT_HOME, and a
    home folder of its own (where ~/.tgkit-home is written)."""
    user = tmp_path / "user"
    user.mkdir()
    for k in ("TGKIT_HOME", "TG_API_ID", "TG_API_HASH", "TGKIT_API_ID", "TGKIT_API_HASH"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("USERPROFILE", str(user))
    monkeypatch.setenv("HOME", str(user))
    monkeypatch.setenv("TGKIT_NO_REVEAL", "1")
    monkeypatch.setenv("TGKIT_EXPORT_DIR", str(tmp_path / "exports"))
    assert config.pointer_file() == user / ".tgkit-home"
    app = App(config.home(), claude=ClaudeRunner(cmd=[]), token=TOKEN)
    httpd = serve(app)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield app, f"http://127.0.0.1:{httpd.server_address[1]}", tmp_path
    httpd.shutdown()


def test_setup_in_the_window_starts_with_the_data_folder(fresh_server, monkeypatch):
    """Gate note 5: keep the folder TG Kit uses now, or pick another, like `tgkit setup`."""
    app, base, tmp_path = fresh_server
    st = json.loads(call(base, "/api/setup/state")[1])
    assert st["fresh"] and not st["home_fixed"] and st["home"] == str(config.default_home())
    assert refused(*call(base, "/api/setup/folder", {"choice": "other", "path": "relative/folder"}))
    (tmp_path / "a-file").write_text("x", encoding="utf-8")
    assert refused(*call(base, "/api/setup/folder", {"choice": "other", "path": str(tmp_path / "a-file")}))
    picked = tmp_path / "Dữ liệu TG Kit"
    code, body = call(base, "/api/setup/folder", {"choice": "other", "path": f'"{picked}"'})
    st = json.loads(body)
    assert code == 200 and st["home"] == str(picked) and picked.is_dir() and app.home == picked
    assert config.pointer_file().read_text(encoding="utf-8").strip() == str(picked)
    assert config.home() == picked  # every later run, the CLI too, finds it
    call(base, "/api/setup/codes", {"api_id": "1234567", "api_hash": "ab" * 16})
    assert config.load(picked)["api_id"] == "1234567"  # the next steps save there
    assert not json.loads(call(base, "/api/setup/state")[1])["fresh"]
    code, body = call(base, "/api/setup/folder", {"choice": "keep"})
    assert code == 200 and json.loads(body)["home"] == str(picked)
    monkeypatch.setenv("TGKIT_HOME", str(tmp_path / "fixed"))
    assert json.loads(call(base, "/api/setup/state")[1])["home_fixed"]
    assert refused(*call(base, "/api/setup/folder", {"choice": "keep"}))
