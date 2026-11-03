"""0.2.7: bring in a tg-crawl copy (`tgkit import-crawl`, Settings, Coming from tg-crawl)
and search with a pattern (`tgkit search --regex`, the window's Pattern (advanced)).

The tg-crawl copy here is synthetic (same shapes as tg-crawl's out/ folder, invented
chats and people), built under the test's tmp folder. Telegram is a stand-in."""

import hashlib
import json
import re
import time
from pathlib import Path

import pytest

import fixture_data
from test_app_api import TOKEN, call, server  # noqa: F401  (server: the window's server, a fixture)
from test_v024 import codes, use  # noqa: F401  (codes: a fixture)
from test_v026 import Msg, Tele, ctx_of, lines
from tgkit import __version__, claude, crawlimport, engine, export, net
from tgkit.errors import UserError, localize
from tgkit.store import Store, SyncLock

ROOT = Path(__file__).resolve().parents[1]
SRC = str(ROOT / "src")


def _row(i, text, sender=201, media=None, media_path=None):
    return {"id": i, "date": f"2026-10-{min(28, i % 28 + 1):02d}T08:00:00+00:00", "text": text, "views": None,
            "forwards": None, "reply_to_msg_id": None, "sender_id": sender, "grouped_id": None,
            "media": media, "media_path": media_path}


def _chat(out: Path, name: str, meta: dict | None, rows: list, raw_tail: str = "") -> Path:
    d = out / name
    d.mkdir(parents=True)
    if meta is not None:
        (d / "channel.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    body = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in sorted(rows, key=lambda r: -r["id"]))
    (d / "messages.jsonl").write_text(body + raw_tail, encoding="utf-8")
    return d


def make_crawl(root: Path, gap: bool = False, bad: bool = True) -> Path:
    """A tg-crawl folder: out/ with chats named by id and by title, loose files and a .partial copy."""
    crawl = root / "tg-crawl"
    out = crawl / "out"
    out.mkdir(parents=True)
    pdf = {"kind": "MessageMediaDocument", "mime_type": "application/pdf", "size": 9, "file_name": "ke-hoach.pdf"}
    a = _chat(out, "7001", {"id": 7001, "type": "Channel", "title": "Nhóm Thử Nghiệm", "username": None},
              [_row(3, "chào cả nhóm"), _row(4, "kế hoạch quý 4", media=pdf, media_path="7001\\media\\ke-hoach.pdf"),
               _row(5, "Hóa đơn số 42 đã gửi")])
    (a / "media").mkdir()
    (a / "media" / "ke-hoach.pdf").write_bytes(b"%PDF fake")
    _chat(out, "Demo_Group_Line", {"id": 7002, "type": "Chat", "title": "Demo Group Line", "username": None},
          [_row(10, "first"), _row(11, "invoice 77 attached"), _row(12, "thanks", sender=202)])
    # the same chat as the fixture's Du_an_Alpha (id 1001): 25 and 26 are there already, 27 is newer
    _chat(out, "Du_an_Alpha_crawl", {"id": 1001, "type": "Channel", "title": "Dự án Alpha: Kế hoạch"},
          [_row(25, "OTHER TEXT for 25"), _row(26, "OTHER TEXT for 26"), _row(27, "tin mới từ tg-crawl")])
    if gap:  # Team_Beta (id 2002) ends at 53 here; this copy starts at 60
        _chat(out, "Team_Beta_crawl", {"id": 2002, "type": "Chat", "title": "Team Beta"},
              [_row(60, "after a hole"), _row(61, "more")])
    if bad:
        _chat(out, "7003", {"id": 7003, "type": "Chat", "title": "Broken Chat"}, [_row(1, "ok")],
              raw_tail='{"id": 2, "text": "cut in ha\n')
    _chat(out, "Demo_Group_Line.partial-20261001", {"id": 7002, "type": "Chat"}, [_row(9, "half")])
    (out / "senders.json").write_text(json.dumps({"201": "Giang Hoàng", "202": "Hà Vũ", "102": "WRONG NAME"},
                                                 ensure_ascii=False), encoding="utf-8")
    (out / "hits.tsv").write_text("x\ty\n", encoding="utf-8")
    (out / "invoice.hits.txt").write_text("hit\n", encoding="utf-8")
    (out / "summary.backup-20261001.json").write_text("[]", encoding="utf-8")
    return crawl


def tree_hash(root: Path) -> dict:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def folder_of(home, cid) -> str:
    return next(d.name for d in Store(home).chat_dirs() if Store(home).meta(d).get("id") == cid)


def row_of(res, cid_title):
    return next(r for r in res["rows"] if r["chat"] == cid_title)


# ------------------------------------------------------------ preview


def test_preview_counts_chats_messages_files_and_skips(home, tmp_path, tg):
    crawl = make_crawl(tmp_path)
    plan = crawlimport.preview(home, crawl)  # the tg-crawl folder itself: its out/ is found
    assert plan["folder"] == str(crawl / "out")
    names = {c["name"]: c for c in plan["chats"]}
    assert set(names) == {"7001", "Demo_Group_Line", "Du_an_Alpha_crawl", "7003"}
    assert names["7003"]["bad"] == 1 and names["Du_an_Alpha_crawl"]["in_tgkit"]
    assert plan["messages"] == 9 and plan["files"] == 1  # the damaged chat is not counted
    skipped = {s["name"]: s["code"] for s in plan["skipped"]}
    assert skipped == {"hits.tsv": "loose", "invoice.hits.txt": "loose", "summary.backup-20261001.json": "loose",
                       "Demo_Group_Line.partial-20261001": "partial"}
    before = tree_hash(home)
    code, out, err = tg("import-crawl", str(crawl), "--preview")
    assert code == 0, err
    assert "Found 4 chat(s), 9 message(s), 1 file(s)." in out and "Nothing was copied" in out
    assert "Skipped hits.tsv: a loose file, not a chat." in out
    assert "Skipped Demo_Group_Line.partial-20261001: an unfinished copy" in out
    assert "Broken Chat: 1 line(s) cannot be read" in out
    assert tree_hash(home) == before  # a preview writes nothing


def test_preview_refuses_plainly(home, tmp_path):
    for bad, words in (("", "Pick the tg-crawl folder first."), ("relative/path", "full path"),
                       (str(tmp_path / "nope"), "does not exist"), (str(home), "own data folder")):
        with pytest.raises(UserError) as info:
            crawlimport.preview(home, bad)
        assert words in info.value.message, bad
    (tmp_path / "empty").mkdir()
    with pytest.raises(UserError) as info:
        crawlimport.preview(home, tmp_path / "empty")
    assert "No tg-crawl chats" in info.value.message


# ------------------------------------------------------------ import


def test_import_copies_merges_and_leaves_the_source_byte_identical(home, tmp_path, tg):
    crawl = make_crawl(tmp_path)
    before = tree_hash(crawl)
    alpha_before = {r["id"]: r for r in lines(home, "Du_an_Alpha")}
    code, out, err = tg("import-crawl", str(crawl), "--offline")
    assert code == 1, err  # one chat (the damaged one) needs a look
    assert tree_hash(crawl) == before  # COPIES: every source file byte for byte as it was, none added
    # a new chat: its folder is <Title>-<id>, its file copied (not downloaded), its rows newest first
    a = folder_of(home, 7001)
    assert a.endswith("-7001")
    rows = lines(home, a)
    assert [r["id"] for r in rows] == [5, 4, 3]
    assert rows[1]["media_path"] == str(Path(a) / "media" / "ke-hoach.pdf")
    assert (Store(home).chats / rows[1]["media_path"]).read_bytes() == b"%PDF fake"
    meta = Store(home).meta(Store(home).chats / a)
    assert meta["from_crawl"]["account"] == "not_checked" and meta["title"] == "Nhóm Thử Nghiệm"
    # merged by id: 27 added, 25 and 26 kept as Telegram Assistant had them, no duplicates
    alpha = lines(home, "Du_an_Alpha")
    ids = [r["id"] for r in alpha]
    assert ids == sorted(set(ids), reverse=True) and ids[0] == 27
    assert {r["id"]: r for r in alpha}[26] == alpha_before[26]
    assert net.newest_id(Store(home).chats / "Du_an_Alpha") == 27  # the next sync starts after 27
    # names: the new ones added, a known one never overwritten
    people = json.loads((home / "senders.json").read_text(encoding="utf-8"))
    assert people["201"] == "Giang Hoàng" and people["102"] == fixture_data.PEOPLE["102"]
    # the damaged chat was not imported; the loose files and the .partial copy were skipped, saying so
    assert not any(Store(home).meta(d).get("id") == 7003 for d in Store(home).chat_dirs())
    assert "Skipped hits.tsv" in out and "Skipped Demo_Group_Line.partial-20261001" in out
    assert "WARNING" in out and "could not be read" in out
    assert "The tg-crawl folder was not changed." in out
    assert not list(Store(home).chats.glob(".adding-*"))
    # the imported copy is as old as tg-crawl's file, not shown as freshly updated
    src_mtime = (crawl / "out" / "7001" / "messages.jsonl").stat().st_mtime
    assert abs((Store(home).chats / a / "messages.jsonl").stat().st_mtime - src_mtime) < 2


def test_import_twice_adds_nothing(home, tmp_path):
    crawl = make_crawl(tmp_path, bad=False)
    crawlimport.run_import(home, crawl, check=False)
    first = {d.name: (d / "messages.jsonl").read_bytes() for d in Store(home).chat_dirs()}
    media = sorted(p.name for p in Store(home).chats.rglob("media/*"))
    res = crawlimport.run_import(home, crawl, check=False)
    assert all(r["added"] == 0 for r in res["rows"]) and res["counts"]["ok"] == 3
    assert {d.name: (d / "messages.jsonl").read_bytes() for d in Store(home).chat_dirs()} == first
    assert sorted(p.name for p in Store(home).chats.rglob("media/*")) == media  # same file, not a second copy


def test_next_sync_fetches_only_newer_messages(home, tmp_path, monkeypatch):
    crawl = make_crawl(tmp_path, bad=False)
    crawlimport.run_import(home, crawl, check=False)
    a = Store(home).chats / folder_of(home, 7001)
    acc = use(monkeypatch, Tele({7001: ("Nhóm Thử Nghiệm", [Msg(8), Msg(7), Msg(6), Msg(5), Msg(4), Msg(3)])}))
    net.run(net.sync_chats(ctx_of(home), [a], quiet=True))
    assert [r["id"] for r in lines(home, a.name)] == [8, 7, 6, 5, 4, 3]
    assert acc.asked_offsets  # it asked Telegram, from the watermark 5 (Tele serves only ids above min_id)


def test_a_copy_that_starts_after_the_tgkit_copy_ends_is_not_merged(home, tmp_path):
    crawl = make_crawl(tmp_path, gap=True, bad=False)
    beta = (Store(home).chats / "Team_Beta" / "messages.jsonl").read_bytes()
    res = crawlimport.run_import(home, crawl, check=False)
    row = row_of(res, "Team Beta")
    assert row["status"] == "warning" and "starts at message #60" in row["note"]
    assert (Store(home).chats / "Team_Beta" / "messages.jsonl").read_bytes() == beta
    assert "#60" in crawlimport.render_report(res) or "WARNING" in crawlimport.render_report(res)


def test_a_failed_chat_keeps_its_copy_and_the_others_go_on(home, tmp_path, monkeypatch):
    crawl = make_crawl(tmp_path, bad=False)
    alpha = (Store(home).chats / "Du_an_Alpha" / "messages.jsonl").read_bytes()
    real = crawlimport.atomic_write

    def broken(path, text):
        if Path(path).parent.name == "Du_an_Alpha":
            raise OSError("disk full")
        return real(path, text)
    monkeypatch.setattr(crawlimport, "atomic_write", broken)
    res = crawlimport.run_import(home, crawl, check=False)
    assert row_of(res, "Dự án Alpha: Kế hoạch")["status"] == "failed"
    assert (Store(home).chats / "Du_an_Alpha" / "messages.jsonl").read_bytes() == alpha
    assert row_of(res, "Nhóm Thử Nghiệm")["status"] == "ok" and row_of(res, "Demo Group Line")["status"] == "ok"
    assert not list(Store(home).chats.glob(".adding-*"))


def test_the_check_after_import_warns_when_a_message_is_missing(home, tmp_path, monkeypatch):
    crawl = make_crawl(tmp_path, bad=False)
    real = crawlimport._ids
    monkeypatch.setattr(crawlimport, "_ids", lambda p: real(p) - {4})
    res = crawlimport.run_import(home, crawl, check=False)
    row = row_of(res, "Nhóm Thử Nghiệm")
    assert row["status"] == "warning" and "1 of 3 messages from tg-crawl are not in" in row["note"]
    assert "WARNING" in crawlimport.render_report(res)


def test_import_waits_for_no_one_it_refuses_while_a_sync_runs(home, tmp_path):
    crawl = make_crawl(tmp_path, bad=False)
    lock = SyncLock(home).acquire()
    try:
        with pytest.raises(UserError) as info:
            crawlimport.run_import(home, crawl, check=False)
        assert "already running" in info.value.message
    finally:
        lock.release()
    assert not any(Store(home).meta(d).get("id") == 7001 for d in Store(home).chat_dirs())


def test_chats_are_mapped_to_the_logged_in_account(codes, tmp_path, monkeypatch, tg, capsys):
    home = codes
    (home / "tgkit.session").touch()
    crawl = make_crawl(tmp_path, bad=False)
    acc = use(monkeypatch, Tele({7001: ("Nhóm Thử Nghiệm (new name)", [])}))
    res = crawlimport.run_import(home, crawl)
    assert row_of(res, "Nhóm Thử Nghiệm (new name)")["account"] == "seen"
    other = row_of(res, "Demo Group Line")
    assert other["account"] == "not_seen" and "does not have this chat" in other["note"]
    assert acc.made >= 1
    d = Store(home).chats / folder_of(home, 7002)
    assert Store(home).meta(d)["from_crawl"]["account"] == "not_seen"
    code, out, _ = tg("chats")
    assert "your Telegram account does not have this chat" in out
    # sync skips it plainly, and that is not a failure
    capsys.readouterr()
    assert net.run(net.sync_chats(ctx_of(home), [d], quiet=True)) == 0
    assert "came from tg-crawl" in capsys.readouterr().err


# ------------------------------------------------------------ pattern search


def test_search_regex_finds_what_the_pattern_says(home, tg, tg_json):
    data = tg_json("search", "--regex", r"bi[eê]n b[aả]n h[oọ]p")
    assert data["total"] >= 2 and all("họp" in r["text"].lower() or "hop" in r["text"].lower()
                                      or r["file"] for r in data["results"])
    data = tg_json("find", "--regex", r"^Ok\b")  # the find alias keeps working, with --regex too
    assert [r["id"] for r in data["results"]] == [71]
    assert tg_json("find", "biên bản")["total"] >= 1  # and without it, plain words as before
    code, out, err = tg("search", "--regex", "(unclosed")
    assert code == 1 and "That pattern is not valid" in err
    code, out, _ = tg("search", "--regex", "zzz+qqq")
    assert code == 0 and "No messages match the pattern" in out


def test_a_catastrophic_pattern_is_stopped_in_bounded_time(home):
    fixture_data.write_chat(home, "Slow", {"id": 5005, "type": "Chat", "title": "Slow"},
                            [fixture_data._row(1, 1, 101, "a" * 40 + "!")])
    started = time.monotonic()
    with pytest.raises(UserError) as info:
        engine.regex_find(Store(home), "(a+)+$", chat="Slow", timeout=2)
    assert time.monotonic() - started < 15
    assert "took longer than 2 seconds" in info.value.message
    assert localize(info.value.message, "vi").startswith("Mẫu chạy quá 2 giây")


def test_regex_is_read_only_for_claude_and_import_is_denied(home, tg, monkeypatch, tmp_path):
    assert "mcp__tgkit__search" in claude.ALLOWED and not any("import" in a for a in claude.ALLOWED)
    for shell in ("Bash", "PowerShell"):
        assert shell in claude.DENIED
    assert "import-crawl" in claude.APP_RULES and "--regex" in claude.APP_RULES
    crawl = make_crawl(tmp_path)
    monkeypatch.setenv("TGKIT_READONLY", "1")
    for argv in (["import-crawl", str(crawl)], ["import", str(crawl), "--preview"]):
        code, _, err = tg(*argv)
        assert code == 1 and "read-only" in err, argv
    code, out, err = tg("search", "--regex", "Hóa đơn", "--json")
    assert code == 0, err
    assert not any(Store(home).meta(d).get("id") == 7001 for d in Store(home).chat_dirs())


def test_a_pattern_card_is_saved_with_every_hit(home):
    src = engine.source_of('tgkit search --regex "h[oó]a đ[oơ]n" --json')
    assert src == {"kind": "search", "query": "h[oó]a đ[oơ]n", "chat": None, "since": None, "regex": True}
    doc = export.build(home, "card", card={"type": "hits", "source": src}, lang="en")
    head, rows = doc["table"]
    assert len(rows) >= 1 and any("Hóa đơn" in str(r) for r in rows)


# ------------------------------------------------------------ the window


def _events(text):
    return [json.loads(x) for x in text.splitlines() if x.strip()]


def test_window_pattern_search_and_import(home, server, tmp_path, monkeypatch):
    app, base = server
    monkeypatch.setenv("PYTHONPATH", SRC)  # the window's `python -m tgkit import-crawl` child finds this tgkit
    crawl = make_crawl(tmp_path)
    status, _ = call(base, "/api/import/preview", {"folder": str(crawl)}, token="wrong")
    assert status == 403  # the per-launch token, as for every /api call
    status, body = call(base, "/api/import/preview", {"folder": str(crawl)})
    plan = json.loads(body)
    assert status == 200 and len(plan["chats"]) == 4 and plan["messages"] == 9
    status, body = call(base, "/api/import/preview", {"folder": str(tmp_path / "nope")})
    assert json.loads(body)["user_error"]
    # Pattern (advanced): matched here, never by Claude
    status, body = call(base, "/api/ask", {"text": r"h[oó]a [dđ][oơ]n", "pattern": True})
    evs = _events(body)
    card = next(e for e in evs if e["type"] == "cards")["cards"][0]
    assert card["match"] == "pattern" and card["source"]["regex"] and card["total"] >= 1
    status, body = call(base, "/api/ask", {"text": "(", "pattern": True})
    assert any(e["type"] == "error" and "not valid" in e["message"] for e in _events(body))
    # Import: the window's own child, one row per chat, then the check
    before = tree_hash(crawl)
    status, body = call(base, "/api/import", {"folder": str(crawl)})
    evs = _events(body)
    assert evs[0]["type"] == "running" and any(e["type"] == "plan" for e in evs)
    done = next(e for e in evs if e["type"] == "done")
    assert done["counts"]["ok"] == 3 and done["counts"]["warning"] == 1
    assert tree_hash(crawl) == before
    assert any(Store(home).meta(d).get("id") == 7001 for d in Store(home).chat_dirs())
    assert not app._sync_busy.locked() and app._proc is None


def test_window_import_rows_in_vietnamese(home, server, tmp_path, monkeypatch):
    from test_v023 import call_lang  # noqa: PLC0415

    _, base = server
    monkeypatch.setenv("PYTHONPATH", SRC)
    crawl = make_crawl(tmp_path)
    _, body = call_lang(base, "/api/import", {"folder": str(crawl)}, "vi")
    done = next(e for e in _events(body) if e["type"] == "done")
    broken = next(r for r in done["rows"] if r["chat"] == "Broken Chat")
    assert broken["note"].startswith("1 dòng trong messages.jsonl")
    assert "Máy này chưa đăng nhập Telegram" in done["account_note"]


def test_window_has_the_pattern_toggle_and_the_import_section_in_both_languages():
    html = (ROOT / "src" / "tgkit" / "web" / "index.html").read_text(encoding="utf-8")
    js = (ROOT / "src" / "tgkit" / "web" / "static" / "app.js").read_text(encoding="utf-8")
    for ident in ("patternOn", "importSec", "importDir", "importPick", "importCheck", "importGo", "importStop",
                  "importRows", "suCrawl"):
        assert f'id="{ident}"' in html, ident
    assert "Pattern (advanced)" in html and "Coming from tg-crawl" in html
    for key in ("patternOn", "secImport", "importHelp", "importFound", "importDone", "res_warning", "skip_partial",
                "suCrawlBtn"):
        assert len(re.findall(rf"\b{key}: [\"'`(]", js)) == 2, key  # en and vi (a string or a function)
    assert "Mẫu (nâng cao)" in js and "Chuyển từ tg-crawl" in js


# ------------------------------------------------------------ version and docs


def test_version_027_everywhere_and_docs():
    assert tuple(int(x) for x in __version__.split(".")) >= (0, 2, 7)  # 0.2.8 and later keep every 0.2.7 test
    assert f'version = "{__version__}"' in (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"] == __version__
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "## Coming from tg-crawl" in readme and "tgkit import-crawl" in readme
    book = (ROOT / "src" / "tgkit" / "playbook.md").read_text(encoding="utf-8")
    assert "tgkit search --regex" in book and "import-crawl" in book
    assert "--regex" in (ROOT / "commands" / "tg-search.md").read_text(encoding="utf-8")
    doc = (ROOT / "docs" / "MAINTENANCE.md").read_text(encoding="utf-8")
    assert "R18" in doc and "crawlimport.py" in doc
    for path in ("README.md", "docs/MAINTENANCE.md", "src/tgkit/playbook.md", "src/tgkit/crawlimport.py",
                 "src/tgkit/web/static/app.js", "src/tgkit/web/index.html", "tests/test_v027.py"):
        text = (ROOT / path).read_text(encoding="utf-8")
        assert chr(0x2014) not in text and chr(0x2013) not in text, path
