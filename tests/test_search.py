from tgkit.store import fold, matcher


def test_fold_and_matcher():
    assert fold("Biên Bản Đã") == "bien ban da"
    assert matcher("bien ban")("Biên bản họp")
    assert matcher("biên bản")("BIÊN BẢN")
    assert not matcher("biến")("Biên bản")
    assert matcher("((")("a (( b")
    assert matcher(r"\d{2}-\d{2}")("họp 28-10")


def test_search_without_accents_finds_names_and_titles(home, tg):
    code, out, err = tg("search", "bien ban")
    assert code == 0, err
    assert "Biên bản họp đây ạ" in out
    assert "Dũng Phạm" in out
    assert "Dự án Alpha: Kế hoạch" in out


def test_search_newest_first_and_file_names(home, tg):
    code, out, err = tg("search", "28-10")
    assert code == 0
    assert "Biên bản họp 28-10.pdf" in out
    code, out, err = tg("search", "bien ban")
    assert out.index("Biên bản họp đây ạ") < out.index("nhờ em gửi biên bản")


def test_search_json(home, tg_json):
    j = tg_json("search", "hóa đơn", "--chat", "ke toan")
    assert j["total"] == 1
    assert j["results"][0]["sender"] == "Bình Trần"
    assert j["results"][0]["chat"] == "Phòng Kế toán"


def test_search_no_hits_gives_a_tip(home, tg):
    code, out, err = tg("search", "không có gì như vậy")
    assert code == 0
    assert "--online" in out


def test_search_since(home, tg_json):
    assert tg_json("search", "alpha", "--since", "7d")["total"] == 1
    assert tg_json("search", "alpha")["total"] == 2


def test_unknown_chat_lists_known_ones(home, tg):
    code, out, err = tg("show", "khong ton tai")
    assert code == 1
    assert "Team Beta" in err and "Traceback" not in err


def test_show_thread_and_sender(home, tg):
    code, out, err = tg("show", "alpha", "--thread", "21")
    assert "(2 shown)" in out
    code, out, err = tg("show", "alpha", "--sender", "binh")
    assert "(4 shown)" in out


def test_media_lists_full_paths(home, tg):
    code, out, err = tg("media", "alpha")
    assert code == 0
    assert str(home / "chats" / "Du_an_Alpha" / "media") in out
