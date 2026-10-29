from tgkit import cli, digest


def test_help_lists_everyday_commands(tg):
    code, out, err = tg("help")
    assert code == 0
    for word in ("search", "summary", "tasks", "sync", "setup"):
        assert f"tgkit {word}" in out


def test_unknown_command(tg):
    code, out, err = tg("frobnicate")
    assert code == 2
    assert "frobnicate" in err and "tgkit help" in err


def test_where(home, tg):
    code, out, err = tg("where")
    assert code == 0
    assert str(home) in out and "TGKIT_HOME" in out


def test_bare_tgkit_opens_the_menu_and_quits(home, tg, monkeypatch):
    replies = iter(["6", "", "0"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(replies))
    code, out, err = tg()
    assert code == 0
    assert "Search messages" in out and "Team Beta" in out


def test_menu_ends_on_closed_input(home, tg, monkeypatch):
    def closed(prompt=""):
        raise EOFError
    monkeypatch.setattr("builtins.input", closed)
    code, out, err = tg()
    assert code == 0 and "Bye" in out


def test_unexpected_error_is_logged_not_dumped(home, tg, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("simulated")
    monkeypatch.setattr(digest, "build", boom)
    code, out, err = tg("summary")
    assert code == 1
    assert "something went wrong" in err and "Traceback" not in err
    log = home / "logs" / "last-error.txt"
    assert log.exists() and "Traceback" in log.read_text(encoding="utf-8")


def test_sync_without_codes_points_to_setup(home, tg):
    code, out, err = tg("sync")
    assert code == 1 and "tgkit setup" in err


def test_chats_inventory(home, tg):
    code, out, err = tg("chats")
    assert code == 0
    assert "Phòng Kế toán" in out and "no messages yet" in out


def test_main_is_the_entry_point():
    assert callable(cli.main)
