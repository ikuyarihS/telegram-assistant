"""Gemini mode with a fake Gemini CLI (tests/fake_gemini.py): no network, no Google login."""

import json
import os
import sys
import time
import tomllib
from pathlib import Path

import pytest

from tgkit import ai, claude, gemini, usage
from tgkit.appserver import App
from tgkit.claude import ClaudeRunner
from tgkit.gemini import GeminiRunner

HERE = Path(__file__).resolve().parent
STUB = str(HERE / "fake_gemini.py")
CLAUDE_STUB = str(HERE / "fake_claude.py")
NPM_STUB = str(HERE / "fake_npm.py")


def runner():
    return GeminiRunner(cmd=[sys.executable, STUB])


@pytest.fixture(autouse=True)
def tgkit_importable(monkeypatch):
    """The fake CLI really runs `python -m tgkit`: it must find this source tree."""
    src = str(HERE.parent / "src")
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([src, os.environ.get("PYTHONPATH", "")]))


@pytest.fixture
def signed_in(tmp_path, monkeypatch):
    """A Gemini CLI home where the user signed in with Google (settings + account file)."""
    ghome = tmp_path / "ghome"
    (ghome / ".gemini").mkdir(parents=True)
    (ghome / ".gemini" / "settings.json").write_text(
        '{\n  // the CLI allows comments\n  "security": {"auth": {"selectedType": "oauth-personal"}}\n}', encoding="utf-8")
    (ghome / ".gemini" / "google_accounts.json").write_text('{"active": "lan@example.com", "old": []}', encoding="utf-8")
    monkeypatch.setenv("GEMINI_CLI_HOME", str(ghome))
    return ghome


def gemini_app(home, **kw):
    return App(home, claude=ClaudeRunner(cmd=[]), gemini=runner(), token="t", **kw)


def log_lines(path):
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]


# ---------------------------------------------------------------- the runner

def test_runner_streams_the_same_events_as_claude(home, tmp_path, monkeypatch):
    log = tmp_path / "gemini.log"
    monkeypatch.setenv("FAKE_GEMINI_LOG", str(log))
    evs = list(runner().stream("tìm biên bản", home, None, ["Team Beta"]))
    kinds = [e["type"] for e in evs]
    assert kinds[0] == "session" and kinds[-1] == "final" and set(kinds) <= set(ai.EVENTS)
    assert "".join(e["text"] for e in evs if e["type"] == "token").startswith("Đang tìm...")
    assert [e for e in evs if e["type"] == "step"][0]["command"].startswith("tgkit search")
    assert [e for e in evs if e["type"] == "step_done"][0]["ok"] is True
    assert [e for e in evs if e["type"] == "tool_json"][0]["data"]["total"] == 1
    used = [e for e in evs if e["type"] == "usage"][0]["usage"]
    assert used == {"input": 200, "output": 45, "cache_read": 3000, "cache_write": 0, "models": ["gemini-2.5-pro"]}
    assert evs[-1]["text"] == "Có 1 tin nhắn nhắc đến biên bản họp." and evs[-1]["session"] == "gem-sess-1"
    # Claude's fake gives the same kinds of events for the same question: one interface.
    cl = list(ClaudeRunner(cmd=[sys.executable, CLAUDE_STUB]).stream("tìm biên bản", home, None, []))
    assert {e["type"] for e in cl} == set(kinds)

    run = log_lines(log)[0]
    argv = run["argv"]
    assert "-p" in argv and argv[argv.index("-o") + 1] == "stream-json"
    assert argv[argv.index("--approval-mode") + 1] == "default"
    assert "--yolo" not in argv and "-y" not in argv and "yolo" not in argv and "--resume" not in argv
    policy = Path(argv[argv.index("--admin-policy") + 1])
    assert argv[argv.index("--policy") + 1] == str(policy) and policy.parent == home / "app" / "gemini"
    assert run["readonly"] == "1" and run["no_relaunch"] == "true"
    assert Path(run["cwd"]) == home / "app" / "gemini"
    assert "tìm biên bản" in run["stdin"] and "Team Beta" in run["stdin"]
    assert "run_shell_command" in run["stdin"] and "## In the Telegram Assistant app" in run["stdin"]


def test_policy_allows_only_tgkit_reads(home):
    rules = tomllib.loads(gemini.policy_text())["rule"]
    allow = [r for r in rules if r["decision"] == "allow"]
    assert len(allow) == 1 and allow[0]["toolName"] == "run_shell_command"
    assert sorted(allow[0]["commandPrefix"]) == ai.READ_PREFIXES
    assert all(p.startswith("tgkit ") for p in ai.READ_PREFIXES)
    assert not any(w in p.split() for p in ai.READ_PREFIXES for w in ("sync", "setup", "add", "logout", "export"))
    star = [r for r in rules if r["toolName"] == "*" and "mcpName" not in r]
    assert star and star[0]["decision"] == "deny" and star[0]["priority"] < allow[0]["priority"]
    denies = [r for r in rules if r["decision"] == "deny" and "commandRegex" in r]
    assert all(r["priority"] > allow[0]["priority"] for r in denies)
    text = " ".join(r["commandRegex"] for r in denies)
    for word in ai.WRITE_COMMANDS + ai.TASK_WRITES:
        assert word in text
    # the same lists as Claude's rules: one source
    assert claude.READ_COMMANDS is ai.READ_COMMANDS and claude.DENIED_COMMANDS is ai.DENIED_COMMANDS
    assert gemini.APP_RULES == ai.app_rules("the run_shell_command tool")
    assert claude.APP_RULES == ai.app_rules("your shell tool (Bash or PowerShell, whichever you have)")


@pytest.mark.parametrize("env", [
    {"FAKE_GEMINI_RUN": "tasks done 1"},
    {"FAKE_GEMINI_RUN": "tasks --json done 1"},
    {"FAKE_GEMINI_RUN": "sync"},
    {"FAKE_GEMINI_RUN": "import-crawl x"},
    {"FAKE_GEMINI_RUN": "export tasks --json"},
    {"FAKE_GEMINI_CMD": "type C:\\Users\\x\\secrets.txt"},
    {"FAKE_GEMINI_CMD": 'tgkit search "x" --json && del notes.txt'},
    {"FAKE_GEMINI_TOOL": "write_file"},
    {"FAKE_GEMINI_TOOL": "web_fetch"},
])
def test_policy_refuses_writes_and_other_tools(home, monkeypatch, env):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    before = (home / "tasks.json").read_bytes() if (home / "tasks.json").exists() else None
    evs = list(runner().stream("đánh dấu xong việc 1", home))
    done = [e for e in evs if e["type"] == "step_done"][0]
    assert done["ok"] is False
    assert not [e for e in evs if e["type"] == "tool_json"]
    after = (home / "tasks.json").read_bytes() if (home / "tasks.json").exists() else None
    assert after == before


def test_readonly_tgkit_refuses_even_when_the_cli_lets_it_through(home, monkeypatch, tg):
    assert tg("tasks")[0] == 0
    before = (home / "tasks.json").read_bytes()
    monkeypatch.setenv("FAKE_GEMINI_IGNORE_POLICY", "1")
    for run in ("tasks --json done 1", "tasks drop 1", "sync", "export tasks"):
        monkeypatch.setenv("FAKE_GEMINI_RUN", run)
        evs = list(runner().stream("xong việc 1", home))
        done = [e for e in evs if e["type"] == "step_done"][0]
        assert done["ok"] is False and "read-only" in done["note"], run
    assert (home / "tasks.json").read_bytes() == before


def test_runner_stops_when_a_forbidden_command_ran(home, monkeypatch):
    monkeypatch.setenv("FAKE_GEMINI_IGNORE_POLICY", "1")
    monkeypatch.setenv("FAKE_GEMINI_CMD", "type secrets.txt")
    evs = list(runner().stream("đọc tệp", home))
    assert evs[-1]["type"] == "error" and evs[-1]["reason"] == "blocked"
    assert not [e for e in evs if e["type"] == "final"]


def test_allowed_command_matches_the_policy_words():
    for ok in ('tgkit search "biên bản" --json', 'tgkit search --regex "a|b" --json', "tgkit tasks",
               "tgkit tasks --chat x --since 7d --json", "tgkit today --json", "tgkit chats-log --json",
               "tgkit ai-status --json", "tgkit claude-status --json"):
        assert ai.allowed_command(ok), ok
    for bad in ("tgkit sync", "tgkit tasks done 1", "tgkit tasks --json drop 2", "tgkit export tasks",
                "tgkit search x && del y", "tgkit search x > out.txt", "cat x", "tgkit search $(whoami)",
                "tgkit import-crawl D:/x", "tgkit setup", "tgkit logout --yes"):
        assert not ai.allowed_command(bad), bad


def test_auth_and_quota_failures(home, monkeypatch, signed_in):
    assert gemini.status([sys.executable, STUB], home)["state"] == "connected"
    monkeypatch.setenv("FAKE_GEMINI_MODE", "auth")
    evs = list(runner().stream("hi", home))
    assert evs[-1]["type"] == "error" and evs[-1]["reason"] == "login"
    assert evs[-1]["message"] == "Gemini is not signed in on this computer."
    st = gemini.status([sys.executable, STUB], home)
    assert st["state"] == "not_logged_in" and st["fix"]
    # Signing in again rewrites the account file: the failure no longer counts.
    acc = signed_in / ".gemini" / "google_accounts.json"
    acc.write_text('{"active": "lan@example.com"}', encoding="utf-8")
    os.utime(acc, ns=(time.time_ns(), time.time_ns() + 5_000_000_000))
    assert gemini.status([sys.executable, STUB], home)["state"] == "connected"
    monkeypatch.setenv("FAKE_GEMINI_MODE", "quota")
    evs = list(runner().stream("hi", home))
    assert evs[-1]["reason"] == "limit"


def test_stop_ends_the_gemini_process(home, monkeypatch):
    monkeypatch.setenv("FAKE_GEMINI_SLEEP", "30")
    r = runner()
    gen = r.stream("tìm biên bản", home)
    for ev in gen:
        if ev["type"] == "step_done":
            break
    t0 = time.monotonic()
    gen.close()  # what the window's Stop does: the answer stream is dropped
    assert time.monotonic() - t0 < 10
    assert r.proc is not None and r.proc.poll() is not None


# ---------------------------------------------------------------- status and finding it

def test_status_states(home, tmp_path, monkeypatch):
    monkeypatch.setattr(gemini, "node_info", lambda: {"found": False, "path": None, "version": None, "ok": False})
    st = gemini.status([], home)
    assert st["state"] == "no_node" and "Node.js" in st["fix"][0]
    monkeypatch.setattr(gemini, "node_info", lambda: {"found": True, "path": "node", "version": "18.1.0", "ok": False})
    assert "too old" in gemini.status([], home)["fix"][0]
    monkeypatch.setattr(gemini, "node_info", lambda: {"found": True, "path": "node", "version": "22.1.0", "ok": True})
    monkeypatch.setenv("TGKIT_NPM", NPM_STUB)
    st = gemini.status([], home)
    assert st["state"] == "missing" and st["can_install"] is True
    ghome = tmp_path / "empty"
    ghome.mkdir()
    monkeypatch.setenv("GEMINI_CLI_HOME", str(ghome))
    st = gemini.status([sys.executable, STUB], home)
    assert st["installed"] and st["version"] == "0.66.0" and st["state"] == "not_logged_in"
    # an auth type but no Google account recorded: not signed in (not a file-exists guess)
    (ghome / ".gemini").mkdir()
    (ghome / ".gemini" / "settings.json").write_text('{"security":{"auth":{"selectedType":"oauth-personal"}}}',
                                                     encoding="utf-8")
    (ghome / ".gemini" / "google_accounts.json").write_text('{"active": null}', encoding="utf-8")
    assert gemini.status([sys.executable, STUB], home)["state"] == "not_logged_in"
    (ghome / ".gemini" / "google_accounts.json").write_text('{"active": "an@example.com"}', encoding="utf-8")
    st = gemini.status([sys.executable, STUB], home)
    assert st["state"] == "connected" and st["account"] == "an@example.com"
    monkeypatch.setenv("GEMINI_CLI_HOME", str(tmp_path / "none"))
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    assert gemini.status([sys.executable, STUB], home)["state"] == "connected"


def test_find_gemini_runs_node_with_the_script_not_the_cmd_shim(tmp_path, monkeypatch):
    monkeypatch.delenv("TGKIT_GEMINI", raising=False)
    prefix = tmp_path / "npm"
    prefix.mkdir()
    monkeypatch.setenv("FAKE_NPM_PREFIX", str(prefix))
    import subprocess
    subprocess.run([sys.executable, NPM_STUB, "install", "-g", "@google/gemini-cli"], check=True)
    node = tmp_path / "node.exe"
    node.write_text("", encoding="utf-8")
    monkeypatch.setenv("TGKIT_NODE", str(node))
    monkeypatch.setattr(gemini.shutil, "which", lambda name: None)
    monkeypatch.setattr(gemini, "npm_dirs", lambda: [prefix])
    cmd = gemini.find_gemini()
    assert cmd == [str(node), str(prefix / "node_modules" / "@google" / "gemini-cli" / "bundle" / "gemini.js")]
    monkeypatch.setenv("TGKIT_GEMINI", "off")
    assert gemini.find_gemini() is None


def test_pick():
    assert ai.pick("auto", True, None, True, "connected") == "claude"  # both: Claude stays the default
    assert ai.pick("auto", True, "not_logged_in", True, "connected") == "gemini"
    assert ai.pick("auto", True, "not_logged_in", True, "not_logged_in") == "claude"
    assert ai.pick("auto", False, None, True, "connected") == "gemini"
    assert ai.pick("auto", False, None, True, "not_logged_in") is None
    assert ai.pick("gemini", True, "connected", True, "not_logged_in") == "gemini"
    assert ai.pick("gemini", True, "connected", False, None) is None
    assert ai.pick("claude", False, None, True, "connected") is None
    assert ai.pick("off", True, "connected", True, "connected") is None


# ---------------------------------------------------------------- the window

def test_app_answers_with_gemini_and_resumes(home, tmp_path, monkeypatch, signed_in):
    log = tmp_path / "gemini.log"
    monkeypatch.setenv("FAKE_GEMINI_LOG", str(log))
    app = gemini_app(home)
    assert app.state()["ai"] == "gemini"
    evs = list(app.ask({"text": "tìm biên bản"}))
    assert {"type": "mode", "mode": "gemini"} in evs
    done = evs[-1]
    assert done["type"] == "done" and done["mode"] == "gemini" and done["text"] == "Có 1 tin nhắn nhắc đến biên bản họp."
    cards = [c for e in evs if e["type"] == "cards" for c in e["cards"]]
    assert cards[0]["type"] == "hits"
    conv_id = evs[0]["id"]
    conv = app.convs.get(conv_id)
    assert conv["gemini_session"] == "gem-sess-1" and conv["claude_session"] is None
    assert conv["turns"][-1]["mode"] == "gemini" and conv["turns"][-1]["usage"]["cache_read"] == 3000
    row = usage.entries(home)[-1]
    assert row["provider"] == "gemini" and row["model"] == "gemini-2.5-pro" and row["input"] == 200
    list(app.ask({"text": "còn gì nữa", "conversation": conv_id}))
    second = log_lines(log)[1]
    assert second["argv"][second["argv"].index("--resume") + 1] == "gem-sess-1"
    assert "## In the Telegram Assistant app" not in second["stdin"]


def test_app_falls_back_offline_when_gemini_is_not_signed_in(home, monkeypatch, signed_in):
    monkeypatch.setenv("FAKE_GEMINI_MODE", "auth")
    app = App(home, claude=ClaudeRunner(cmd=[]), gemini=runner(), token="t")
    app.save_settings({"ai_provider": "gemini"})
    evs = list(app.ask({"text": "tìm biên bản"}))
    notice = [e for e in evs if e["type"] == "notice"][0]
    assert notice["text"] == "Gemini is not signed in on this computer. Answered without Gemini."
    assert evs[-1]["type"] == "done" and evs[-1]["mode"] == "offline"
    assert app.gemini_status()["state"] == "not_logged_in"
    from tgkit.errors import localize
    assert localize(notice["text"], "vi") == "Gemini chưa đăng nhập trên máy này. Đã trả lời không dùng Gemini."


def test_choice_off_and_claude_default(home, monkeypatch, signed_in):
    app = App(home, claude=ClaudeRunner(cmd=[sys.executable, CLAUDE_STUB]), gemini=runner(), token="t")
    assert app.provider() == "claude"  # both installed: Claude stays the default
    evs = list(app.ask({"text": "tìm biên bản"}))
    assert evs[-1]["mode"] == "claude"
    app.save_settings({"ai_provider": "gemini"})
    assert app.provider() == "gemini" and app.settings()["ai_provider"] == "gemini"
    app.save_settings({"ai_provider": "off"})
    assert app.provider() is None
    evs = list(app.ask({"text": "tìm biên bản"}))
    assert evs[-1]["mode"] == "offline"
    from tgkit.errors import UserError
    with pytest.raises(UserError):
        app.save_settings({"ai_provider": "chatgpt"})


def test_install_reports_failure_unless_gemini_is_really_there(home, tmp_path, monkeypatch):
    prefix = tmp_path / "npm"
    prefix.mkdir()
    monkeypatch.setenv("FAKE_NPM_PREFIX", str(prefix))
    monkeypatch.setenv("TGKIT_NPM", NPM_STUB)
    monkeypatch.delenv("TGKIT_GEMINI", raising=False)
    monkeypatch.setattr(gemini.shutil, "which", lambda name: None)
    monkeypatch.setattr(gemini, "npm_dirs", lambda: [prefix])
    node = tmp_path / "node.exe"
    node.write_text("", encoding="utf-8")
    monkeypatch.setenv("TGKIT_NODE", str(node))
    app = App(home, claude=ClaudeRunner(cmd=[]), gemini=GeminiRunner(cmd=[]), token="t")
    monkeypatch.setenv("FAKE_NPM_MODE", "fail")
    res = app.gemini_install()
    assert res["ok"] is False and "network" in res["detail"]
    monkeypatch.setenv("FAKE_NPM_MODE", "nothing")  # npm said fine, but no gemini: not done
    res = app.gemini_install()
    assert res["ok"] is False and res["message"] == "Gemini could not be installed."
    monkeypatch.delenv("FAKE_NPM_MODE")
    res = app.gemini_install()
    assert res["ok"] is True and app.gemini.cmd[0] == str(node)


def test_login_and_node_open_the_right_thing(home, monkeypatch):
    app = gemini_app(home)
    res = app.gemini_login()
    assert res["ok"] and res["argv"] == [sys.executable, STUB]
    monkeypatch.setattr(gemini, "winget", lambda: True)
    res = app.gemini_node()
    assert res["how"] == "winget" and res["argv"][:2] == ["winget", "install"] and "OpenJS.NodeJS.LTS" in res["argv"]
    monkeypatch.setattr(gemini, "winget", lambda: False)
    res = app.gemini_node()
    assert res["how"] == "browser" and res["url"] == gemini.NODE_URL
    app2 = App(home, claude=ClaudeRunner(cmd=[]), gemini=GeminiRunner(cmd=[]), token="t")
    assert app2.gemini_login()["ok"] is False
