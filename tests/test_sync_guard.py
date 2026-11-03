"""Sync keeps the original tool's guarantees. A fake Telegram client, no network.

The fake serves messages NEWEST FIRST, like telethon's iter_messages
(reverse=False is its default), and can die after N messages.
"""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from tgkit import net
from tgkit.errors import UserError
from tgkit.store import Store


class Channel:
    def __init__(self, key, ident=9000, title="Fake", username=None):
        self.key = key
        self.id = ident
        self.title = title
        self.username = username
        self.megagroup = True
        self.broadcast = False
        self.participants_count = 3


class Person:
    def __init__(self, first, last=None):
        self.first_name = first
        self.last_name = last


class Msg:
    def __init__(self, ident, text="tin nhắn mới", hours=0.5, sender_id=102, sender=None):
        self.id = ident
        self.message = text
        self.date = datetime.now(timezone.utc) - timedelta(hours=hours)
        self.reply_to_msg_id = None
        self.sender_id = sender_id
        self.sender = sender
        self.grouped_id = None
        self.media = None
        self.views = None
        self.forwards = None


class FakeClient:
    def __init__(self, server, fail_after=None, authorized=True):
        self.server = server          # target -> [Msg], newest first
        self.fail_after = fail_after or {}
        self.authorized = authorized
        self.entities_asked = []

    async def connect(self):
        pass

    async def is_user_authorized(self):
        return self.authorized

    async def disconnect(self):
        pass

    async def get_dialogs(self):
        return []

    async def get_entity(self, target):
        self.entities_asked.append(target)
        if target not in self.server:
            raise ValueError(f"no entity {target}")
        return Channel(target)

    def iter_messages(self, entity, min_id=0, limit=None, search=None):
        msgs = [m for m in self.server[entity.key] if m.id > min_id]
        if search:
            msgs = [m for m in msgs if search.lower() in m.message.lower()]
        if limit:
            msgs = msgs[:limit]
        stop = self.fail_after.get(entity.key)

        async def gen():
            for n, m in enumerate(msgs):
                if stop is not None and n == stop:
                    raise ConnectionResetError("connection lost mid-fetch")
                yield m
            if stop is not None and stop >= len(msgs):
                raise ConnectionResetError("connection lost mid-fetch")
        return gen()

    async def download_media(self, msg, file=None):
        return None


ALPHA = -1001001  # target_for() of the fixture's Du_an_Alpha (Channel id 1001)


def use(monkeypatch, client):
    made = []

    def factory(base, cfg):
        made.append(1)
        return client
    monkeypatch.setattr(net, "make_client", factory)
    return made


def ids(home, folder="Du_an_Alpha"):
    import json
    lines = (home / "chats" / folder / "messages.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(x)["id"] for x in lines if x.strip()]


def test_clean_fetch_prepends_newest_first(home, tg, monkeypatch):
    use(monkeypatch, FakeClient({ALPHA: [Msg(30), Msg(28), Msg(27)]}))
    code, out, err = tg("sync", "alpha")
    assert code == 0, err
    got = ids(home)
    assert got[:4] == [30, 28, 27, 26]
    assert got == sorted(got, reverse=True)
    assert len(got) == len(set(got))
    assert "+3 new" in out
    assert (home / "sync-state.json").exists()


def test_partial_fetch_with_gap_is_discarded_and_watermark_held(home, tg, monkeypatch):
    path = home / "chats" / "Du_an_Alpha" / "messages.jsonl"
    before = path.read_bytes()
    server = {ALPHA: [Msg(30), Msg(29), Msg(28)]}
    use(monkeypatch, FakeClient(server, fail_after={ALPHA: 2}))
    code, out, err = tg("sync", "alpha")
    assert code == 1
    assert "discarding 2" in err
    assert path.read_bytes() == before, "a gapped partial fetch must not be written"
    # The watermark held, so one clean run recovers the whole range.
    use(monkeypatch, FakeClient(server))
    code, out, err = tg("sync", "alpha")
    assert code == 0, err
    assert ids(home)[:4] == [30, 29, 28, 26]


def test_contiguous_partial_is_committed(home, tg, monkeypatch):
    use(monkeypatch, FakeClient({ALPHA: [Msg(28), Msg(27)]}, fail_after={ALPHA: 2}))
    code, out, err = tg("sync", "alpha")
    assert code == 1
    assert ids(home)[:3] == [28, 27, 26]
    assert "partial" in out


def test_zero_row_folder_is_skipped_not_crawled(home, tg, monkeypatch):
    client = FakeClient({ALPHA: [], -2002: [], "ketoan_test": [], 4004: [Msg(1)]})
    use(monkeypatch, client)
    code, out, err = tg("sync")
    assert 4004 not in client.entities_asked
    assert "Skipped Em Test" in err
    assert (home / "chats" / "Empty_Chat" / "messages.jsonl").read_text(encoding="utf-8") == ""


def test_damaged_line_refuses_before_connecting(home, tg, monkeypatch):
    path = home / "chats" / "Du_an_Alpha" / "messages.jsonl"
    path.write_text(path.read_text(encoding="utf-8") + "{damaged\n", encoding="utf-8")
    before = path.read_bytes()
    made = use(monkeypatch, FakeClient({ALPHA: [Msg(30)]}))
    code, out, err = tg("sync", "alpha")
    assert code == 1
    assert "damaged" in err and "Refusing" in err
    assert made == [], "must refuse before any connection"
    assert path.read_bytes() == before


def test_dry_run_writes_nothing(home, tg, monkeypatch):
    path = home / "chats" / "Du_an_Alpha" / "messages.jsonl"
    before = path.read_bytes()
    use(monkeypatch, FakeClient({ALPHA: [Msg(30)]}))
    code, out, err = tg("sync", "alpha", "--dry-run")
    assert code == 0, err
    assert "would fetch 1" in out
    assert path.read_bytes() == before
    assert not (home / "sync-state.json").exists()


def test_not_logged_in_refuses_instead_of_prompting(home, tg, monkeypatch):
    use(monkeypatch, FakeClient({ALPHA: [Msg(30)]}, authorized=False))
    code, out, err = tg("sync")
    assert code == 1
    assert "tgkit setup" in err


def test_add_refuses_existing_folder(home, monkeypatch):
    path = home / "chats" / "Du_an_Alpha" / "messages.jsonl"
    before = path.read_bytes()
    store = Store(home)
    ctx = net.Ctx(home, store, {})
    entity = Channel("x", ident=1001, title="Du an Alpha")
    client = FakeClient({"x": [Msg(1)]})
    with pytest.raises(UserError) as exc:
        asyncio.run(net.add_chat(client, ctx, entity, "Du an Alpha", 100))
    assert "already in Telegram Assistant" in exc.value.message
    assert path.read_bytes() == before


def test_add_builds_a_new_chat_newest_first(home, monkeypatch):
    store = Store(home)
    ctx = net.Ctx(home, store, {})
    entity = Channel("n", ident=5005, title="Nhóm Mới")
    client = FakeClient({"n": [Msg(9, sender=Person("Giang", "Võ"), sender_id=105), Msg(5), Msg(2)]})
    res = asyncio.run(net.add_chat(client, ctx, entity, "Nhóm Mới", 100))
    assert res["messages"] == 3
    folder = home / "chats" / res["folder"]
    assert ids(home, res["folder"]) == [9, 5, 2]
    assert store.title(folder) == "Nhóm Mới"
    assert not list((home / "chats").glob(".adding-*"))
    assert Store(home).sender_name(105) == "Giang Võ"


def test_two_chats_with_one_title_both_get_added(home):
    store = Store(home)
    ctx = net.Ctx(home, store, {})
    first = asyncio.run(net.add_chat(FakeClient({"a": [Msg(3)]}), ctx, Channel("a", 7001, "Team"), "Team", 10))
    second = asyncio.run(net.add_chat(FakeClient({"b": [Msg(4)]}), ctx, Channel("b", 7002, "Team"), "Team", 10))
    assert first["folder"] != second["folder"]
    same = asyncio.run(net.add_chat(FakeClient({"c": [Msg(5)]}), ctx, Channel("c", 7003, "Семья"), "x", 10))
    other = asyncio.run(net.add_chat(FakeClient({"d": [Msg(6)]}), ctx, Channel("d", 7004, "家族"), "y", 10))
    assert same["folder"] != other["folder"]


def test_session_file_alone_is_not_logged_in(home):
    from tgkit import config
    (home / "tgkit.session").write_bytes(b"")
    cfg = config.load(home)
    cfg.pop("me", None)
    config.save(home, cfg)
    assert not config.logged_in(home)
    cfg["me"] = {"id": 1}
    config.save(home, cfg)
    assert config.logged_in(home)


def test_online_search_shows_names(home, tg, monkeypatch):
    found = Msg(31, text="biên bản cũ", sender=Person("Chi", "Lê"), sender_id=103)
    use(monkeypatch, FakeClient({ALPHA: [found]}))
    code, out, err = tg("search", "biên bản", "--chat", "alpha", "--online")
    assert code == 0, err
    assert "Chi Lê" in out and "biên bản cũ" in out
