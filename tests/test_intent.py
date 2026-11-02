import pytest

from tgkit.intent import parse

CHATS = [("Du_an_Alpha", "Dự án Alpha: Kế hoạch"), ("Team_Beta", "Team Beta"),
         ("Phòng_Kế_toán", "Phòng Kế toán"), ("Empty_Chat", "Em Test")]


@pytest.mark.parametrize("text", ["tuần này nhóm Beta bàn gì?", "tuan nay nhom beta ban gi"])
def test_vietnamese_summary_with_and_without_accents(text):
    it = parse(text, CHATS)
    assert (it.kind, it.chat, it.since, it.lang) == ("summary", "Team_Beta", "7d", "vi")


def test_english_tasks_mine():
    it = parse("my open tasks", CHATS)
    assert (it.kind, it.which, it.lang) == ("tasks", "mine", "en")


def test_vietnamese_tasks_mine():
    it = parse("Việc của tôi", CHATS)
    assert (it.kind, it.which, it.lang) == ("tasks", "mine", "vi")


def test_all_tasks():
    assert parse("show all tasks", CHATS).which == "all"


def test_search_keeps_the_words():
    it = parse("find bien ban hop", CHATS)
    assert (it.kind, it.query) == ("search", "bien ban hop")


def test_quoted_search_with_chat():
    it = parse('tìm "Biên bản họp" trong kế toán', CHATS)
    assert (it.kind, it.query, it.chat) == ("search", "Biên bản họp", "Phòng_Kế_toán")


def test_summary_chat_and_days():
    it = parse("summarize ke toan last 30 days", CHATS)
    assert (it.kind, it.chat, it.since) == ("summary", "Phòng_Kế_toán", "30d")


@pytest.mark.parametrize("text,since", [
    ("hôm nay có gì mới", "today"), ("what happened yesterday", "yesterday"),
    ("tháng này nhóm alpha nói gì", "30d"), ("recap last week", "14d"), ("3 ngày qua nhóm alpha", "3d"),
])
def test_time_words(text, since):
    it = parse(text, CHATS)
    assert it.since == since
    assert it.kind == "summary"


def test_chat_and_time_alone_mean_summary():
    it = parse("3 ngày qua nhóm alpha", CHATS)
    assert it.chat == "Du_an_Alpha"


@pytest.mark.parametrize("text", ["sync", "cập nhật tin mới", "get new messages"])
def test_sync(text):
    assert parse(text, CHATS).kind == "sync"


def test_question_words_are_dropped_from_a_plain_search():
    it = parse("Ai biết mật khẩu wifi không?", CHATS)
    assert (it.kind, it.query) == ("search", "mật khẩu wifi")


def test_chats_and_help():
    assert parse("which chats do I have", CHATS).kind == "chats"
    assert parse("help", CHATS).kind == "help"
    assert parse("", CHATS).kind == "help"


def test_ke_alone_does_not_pick_a_chat():
    # "kế" is in two titles; a 2-letter word alone is not enough to choose one.
    it = parse("find ke", CHATS)
    assert it.chat is None
