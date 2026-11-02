"""Claude mode with a fake `claude` (tests/fake_claude.py): no network, no real Claude."""

import json
import os
import re
import shlex
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


def test_claude_session_cannot_change_tasks(home, tg, monkeypatch):
    """Gate NOTE 1: "tgkit tasks --json*" let "tasks --json undo 1" through; now every
    changing word is denied wherever it stands and the tgkit Claude starts refuses it."""
    for cmd in WRITES:
        for shell in ("Bash", "PowerShell"):
            assert not permitted(cmd, shell), (shell, cmd)
    assert tg("tasks")[0] == 0 and tg("tasks", "done", "1")[0] == 0
    before = (home / "tasks.json").read_bytes()
    src = str(Path(__file__).resolve().parent.parent / "src")
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([src, os.environ.get("PYTHONPATH", "")]))
    monkeypatch.setenv("FAKE_CLAUDE_RUN", "tasks --json undo 1")
    evs = list(runner().stream("mở lại việc 1", home, None, []))
    done = [e for e in evs if e["type"] == "step_done"][0]
    assert done["ok"] is False and "read-only" in done["note"], done
    assert (home / "tasks.json").read_bytes() == before
    monkeypatch.setenv("TGKIT_READONLY", "1")
    for argv in (["tasks", "--json", "undo", "1"], ["tasks", "--json", "done", "2"],
                 ["task", "--chat", "alpha", "reopen", "1"], ["tasks", "drop", "1"],
                 ["tasks", "note", "1", "x"], ["tasks", "add", "x"], ["sync"], ["setup"], ["add"]):
        code, out, err = tg(*argv)
        assert code == 1 and "read-only" in err, argv
    assert (home / "tasks.json").read_bytes() == before
    code, out, err = tg("tasks", "--json")
    assert code == 0 and json.loads(out)["tasks"]


def permitted(cmd: str, shell: str = "Bash") -> bool:
    """Claude Code's rule check: `*` stands for any run of characters, a deny rule wins."""
    from tgkit.claude import ALLOWED, DENIED

    def hit(rules):
        for rule in rules:
            if rule.startswith(shell + "(") and rule.endswith(")"):
                pat = rule[len(shell) + 1:-1]
                if re.fullmatch(".*".join(re.escape(p) for p in pat.split("*")), cmd, re.S):
                    return True
        return False
    return hit(ALLOWED) and not hit(DENIED)


FILTERED_READS = ["tgkit tasks", "tgkit tasks --json", "tgkit tasks --json --since 7d",
                  "tgkit tasks --chat alpha --json", "tgkit tasks mine --json --since 7d",
                  'tgkit tasks all --chat "du an" --json', "tgkit tasks open --since 30d --json",
                  "tgkit tasks --since 7d --chat alpha --json"]
WRITES = ["tgkit tasks done 3", "tgkit tasks --json done 1", "tgkit tasks --chat alpha drop 2",
          'tgkit tasks note 3 "sent"', 'tgkit tasks add "x" --due Friday', "tgkit tasks --json undo 1",
          "tgkit tasks --since 7d reopen 1", "tgkit tasks --json add x", "tgkit tasks --json note",
          "tgkit sync", "tgkit setup", "tgkit add"]


def test_filtered_task_reads_run_and_writes_are_denied(home, tg, monkeypatch):
    """Gate 0.2.1 note: "tasks --json --since 7d" and "tasks --chat x --json" were denied."""
    for shell in ("Bash", "PowerShell"):
        for cmd in FILTERED_READS:
            assert permitted(cmd, shell), (shell, cmd)
        for cmd in WRITES:
            assert not permitted(cmd, shell), (shell, cmd)
    monkeypatch.setenv("TGKIT_READONLY", "1")
    for cmd in FILTERED_READS:
        code, out, err = tg(*shlex.split(cmd)[1:])
        assert code == 0, (cmd, err)
        if "--json" in cmd:
            assert isinstance(json.loads(out)["tasks"], list)
    tg("tasks", "--json")
    seen = (home / "tasks.json").read_bytes()
    for cmd in WRITES[:9]:
        code, out, err = tg(*shlex.split(cmd)[1:])
        assert code == 1 and "read-only" in err, cmd
    assert (home / "tasks.json").read_bytes() == seen  # no write form changed a task


def test_claude_session_runs_a_filtered_task_read(home, monkeypatch):
    """The fake Claude really runs `tgkit tasks --chat ... --since ... --json` read-only."""
    src = str(Path(__file__).resolve().parent.parent / "src")
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([src, os.environ.get("PYTHONPATH", "")]))
    monkeypatch.setenv("FAKE_CLAUDE_RUN", "tasks --chat alpha --since 30d --json")
    evs = list(runner().stream("việc tuần này", home, None, []))
    done = [e for e in evs if e["type"] == "step_done"][0]
    assert done["ok"] is True, done
    assert isinstance([e for e in evs if e["type"] == "tool_json"][0]["data"]["tasks"], list)


def test_tgkit_claude_off_switch(monkeypatch):
    from tgkit.claude import find_claude
    monkeypatch.setenv("TGKIT_CLAUDE", "off")
    assert find_claude() is None
