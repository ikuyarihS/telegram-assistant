from pathlib import Path

import pytest

from tgkit import config, wizard


def answers(*values):
    it = iter(values)

    def ask(prompt):
        try:
            return next(it)
        except StopIteration:
            raise EOFError from None
    return ask


def test_parse_picks():
    assert wizard.parse_picks("1, 3-5 8", 10) == [1, 3, 4, 5, 8]
    assert wizard.parse_picks("2;2", 3) == [2]
    assert wizard.parse_picks("", 5) == []
    assert wizard.parse_picks("0", 5) is None
    assert wizard.parse_picks("4-9", 5) is None
    assert wizard.parse_picks("team", 5) is None


def test_credentials_are_checked_in_plain_words():
    said = []
    got = wizard.ask_credentials(
        answers("abc", "1234567", "short", "0123456789abcdef0123456789ABCDEF"), said.append)
    assert got == ("1234567", "0123456789abcdef0123456789ABCDEF")
    text = "\n".join(said)
    assert "https://my.telegram.org" in text
    assert "API development tools" in text
    assert "does not look right" in text


def test_chosen_folder_is_remembered(tmp_path, monkeypatch):
    monkeypatch.delenv("TGKIT_HOME", raising=False)
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    target = tmp_path / "Dữ liệu"
    chosen = wizard.choose_home(answers(str(target)), lambda s: None)
    assert chosen == target and target.is_dir()
    assert config.home() == target
    assert (tmp_path / ".tgkit-home").read_text(encoding="utf-8").strip() == str(target)


def test_enter_keeps_the_default_without_a_pointer(tmp_path, monkeypatch):
    monkeypatch.delenv("TGKIT_HOME", raising=False)
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    chosen = wizard.choose_home(answers(""), lambda s: None)
    assert chosen == tmp_path / "TGKit"
    assert not (tmp_path / ".tgkit-home").exists()


def test_pick_from_numbered_list_with_a_filter():
    dialogs = [
        {"name": "Gia đình", "kind": "group", "entity": None},
        {"name": "Team Beta", "kind": "group", "entity": None},
        {"name": "Kế toán", "kind": "channel", "entity": None, "added": True},
    ]
    said = []
    picked = wizard.pick_dialogs(dialogs, None, answers("beta", "1"), said.append)
    assert [d["name"] for d in picked] == ["Team Beta"]
    assert wizard.pick_dialogs(dialogs, "ke toan", answers("1"), said.append) == []
    assert any("(already added)" in s for s in said)


def test_setup_stops_cleanly_when_nothing_is_typed(home, tg, monkeypatch):
    def no_input(prompt=""):
        raise EOFError
    monkeypatch.setattr("builtins.input", no_input)
    code, out, err = tg("setup")
    assert code == 0
    assert "tgkit setup" in out
    assert "Traceback" not in out + err


def test_saved_credentials_are_used(home):
    config.save(home, {"api_id": "1234567", "api_hash": "0123456789abcdef0123456789abcdef"})
    assert config.credentials(home, config.load(home)) == (1234567, "0123456789abcdef0123456789abcdef")


def test_missing_credentials_point_to_setup(home):
    with pytest.raises(Exception) as exc:
        config.credentials(home, {})
    assert "tgkit setup" in exc.value.render()
