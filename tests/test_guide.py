"""`tgkit guide`: one Claude Code playbook, the same text in every place that shows it."""

import os
import subprocess
import sys
from pathlib import Path

from tgkit import claude, playbook
from tgkit.claude import ClaudeRunner

ROOT = Path(__file__).resolve().parent.parent
STUB = str(ROOT / "tests" / "fake_claude.py")


def skill_body() -> str:
    text = (ROOT / "skills" / "telegram" / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\n")
    end = text.index("\n---\n", 4)
    return text[end + len("\n---\n"):].lstrip("\n")


def test_skill_app_guide_and_command_share_one_text(home, tg, tmp_path, monkeypatch):
    book = playbook.text()
    assert skill_body() == book, "skills/telegram/SKILL.md must be its frontmatter + src/tgkit/playbook.md"
    assert claude.GUIDE.startswith(book)
    code, out, err = tg("guide")
    assert code == 0 and out == book and not err
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(tmp_path / "claude.log"))
    list(ClaudeRunner(cmd=[sys.executable, STUB]).stream("hi", home, None, []))
    written = (home / "app" / "claude" / "tgkit-guide.md").read_text(encoding="utf-8")
    assert written.startswith(book) and written == claude.GUIDE


def test_guide_needs_no_setup_and_no_terminal(tmp_path):
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONIOENCODING", "PYTHONUTF8")}
    env["TGKIT_HOME"] = str(tmp_path / "nothing-here")
    env["PYTHONPATH"] = os.pathsep.join([str(ROOT / "src"), env.get("PYTHONPATH", "")])
    done = subprocess.run([sys.executable, "-m", "tgkit", "guide"], stdin=subprocess.DEVNULL,
                          capture_output=True, env=env, timeout=60)
    assert done.returncode == 0, done.stderr
    out = done.stdout.decode("utf-8").replace("\r\n", "\n")
    assert out == playbook.text()
    assert not (tmp_path / "nothing-here").exists()  # reads no config, creates nothing


def test_playbook_covers_the_owner_list():
    book = " ".join(playbook.text().split())
    for cmd in ('tgkit search "X" --json', "tgkit summary --json", "tgkit tasks --json",
                "tgkit tasks mine --json", "tgkit chats-log --json", "tgkit show", "tgkit media",
                "tgkit chats", "tgkit sync", "--online"):
        assert cmd in book, cmd
    for word in ("synced_at", "stale", "in_reply_to", '"results"', '"tasks"', '"conversations"',
                 "tgkit setup", "tgkit login", "tgkit add", "explicitly asks", "language they wrote in",
                 "plain words", "tgkit guide"):
        assert word in book, word
    assert chr(0x2014) not in claude.GUIDE and chr(0x2013) not in claude.GUIDE
