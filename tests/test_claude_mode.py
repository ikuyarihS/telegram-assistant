"""Claude mode with a fake `claude` (tests/fake_claude.py): no network, no real Claude."""

import json
import sys
from pathlib import Path

from tgkit.appserver import App
from tgkit.claude import ClaudeRunner

STUB = str(Path(__file__).resolve().parent / "fake_claude.py")


def runner():
    return ClaudeRunner(cmd=[sys.executable, STUB])


def test_runner_streams_tokens_steps_and_json(home, tmp_path, monkeypatch):
    log = tmp_path / "claude.log"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    evs = list(runner().stream("tìm biên bản", home, None, ["Team Beta"]))
    kinds = [e["type"] for e in evs]
    assert kinds[0] == "session" and kinds[-1] == "final"
    assert "".join(e["text"] for e in evs if e["type"] == "token").startswith("Đang tìm...")
    step = [e for e in evs if e["type"] == "step"][0]
    assert step["command"].startswith("tgkit search")
    assert [e for e in evs if e["type"] == "step_done"][0]["ok"] is True
    assert [e for e in evs if e["type"] == "tool_json"][0]["data"]["total"] == 1
    run = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    argv = run["argv"]
    assert "-p" in argv and "stream-json" in argv and "--resume" not in argv
    allowed = argv[argv.index("--allowedTools") + 1: argv.index("--disallowedTools")]
    assert allowed and all(a.startswith(("Bash(tgkit ", "PowerShell(tgkit ")) for a in allowed)
    assert not any("sync" in a or "setup" in a or " add" in a for a in allowed)
    denied = argv[argv.index("--disallowedTools") + 1:]
    assert "Write" in denied
    tools = argv[argv.index("--tools") + 1].split(",")
    assert set(tools) == {"Bash", "PowerShell"}
    for shell in tools:  # every shell gets the same allow and deny rules
        assert {a.split("(", 1)[1] for a in allowed if a.startswith(shell + "(")} == \
            {a.split("(", 1)[1] for a in allowed if a.startswith("Bash(")}
        for cmd in ("setup", "add", "sync", "tasks done", "tasks drop", "tasks note", "tasks add"):
            assert f"{shell}(tgkit {cmd}*)" in denied
    assert "tìm biên bản" in run["stdin"] and "Team Beta" in run["stdin"]


def test_app_keeps_the_session_and_resumes_it(home, tmp_path, monkeypatch):
    log = tmp_path / "claude.log"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    app = App(home, claude=runner(), token="t")
    evs = list(app.ask({"text": "tìm biên bản"}))
    assert {"type": "mode", "mode": "claude"} in evs
    done = evs[-1]
    assert done["type"] == "done" and done["mode"] == "claude"
    assert done["text"] == "Có 1 tin nhắn nhắc đến biên bản họp."
    cards = [c for e in evs if e["type"] == "cards" for c in e["cards"]]
    assert cards[0]["type"] == "hits" and cards[0]["items"][0]["id"] == 23
    conv = app.convs.get(evs[0]["id"])
    assert conv["claude_session"] == "sess-1"
    assert conv["turns"][1]["steps"][0]["command"].startswith("tgkit search")

    list(app.ask({"text": "còn tuần trước?", "conversation": conv["id"]}))
    second = json.loads(log.read_text(encoding="utf-8").splitlines()[1])["argv"]
    assert second[second.index("--resume") + 1] == "sess-1"


def test_claude_failure_falls_back_to_offline(home, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "fail")
    app = App(home, claude=runner(), token="t")
    evs = list(app.ask({"text": "việc của tôi"}))
    notice = [e for e in evs if e["type"] == "notice"][0]
    assert "not signed in" in notice["text"] and notice["reason"] == "login"
    assert evs[-1]["type"] == "done" and evs[-1]["mode"] == "offline"
    assert [c for e in evs if e["type"] == "cards" for c in e["cards"]][0]["type"] == "tasks"


def test_offline_choice_skips_claude(home, tmp_path, monkeypatch):
    log = tmp_path / "claude.log"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    app = App(home, claude=runner(), token="t")
    evs = list(app.ask({"text": "summary", "mode": "offline"}))
    assert evs[-1]["mode"] == "offline" and not log.exists()


def test_powershell_session_steps_and_no_dashes(home, monkeypatch):
    """Gate B-1, B-4: a Claude whose shell is PowerShell still runs tgkit; dashes go."""
    monkeypatch.setenv("FAKE_CLAUDE_SHELL", "PowerShell")
    em, en = chr(0x2014), chr(0x2013)
    monkeypatch.setenv("FAKE_CLAUDE_FINAL", f"Có 1 tin {em} biên bản họp 1{en}2.")
    evs = list(runner().stream("tìm biên bản", home, None, []))
    assert [e for e in evs if e["type"] == "step"][0]["command"].startswith("tgkit search")
    assert [e for e in evs if e["type"] == "tool_json"]
    text = [e for e in evs if e["type"] == "final"][0]["text"]
    tokens = "".join(e["text"] for e in evs if e["type"] == "token")
    for s in (text, tokens):
        assert em not in s and en not in s
    assert text == "Có 1 tin - biên bản họp 1-2."


def test_tgkit_claude_off_switch(monkeypatch):
    from tgkit.claude import find_claude
    monkeypatch.setenv("TGKIT_CLAUDE", "off")
    assert find_claude() is None
