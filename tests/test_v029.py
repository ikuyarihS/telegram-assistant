"""0.2.9: Gemini as a second AI beside Claude (ai-status, Settings, setup buttons, version)."""

import json
import re
import sys
import threading
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from tgkit import __version__, ai
from tgkit.appserver import App, serve
from tgkit.claude import ClaudeRunner
from tgkit.gemini import GeminiRunner

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "src" / "tgkit" / "web"
STUB = str(ROOT / "tests" / "fake_gemini.py")
TOKEN = "tok-029"


@pytest.fixture
def server(home):
    app = App(home, claude=ClaudeRunner(cmd=[]), gemini=GeminiRunner(cmd=[sys.executable, STUB]), token=TOKEN)
    httpd = serve(app)
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    yield app, f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def call(base, path, body=None, token=TOKEN):
    req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode("utf-8"),
                                 method="GET" if body is None else "POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("X-TGKit-Token", token)
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            return res.status, json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, None


def test_version_is_029_everywhere():
    assert __version__ == "0.2.9"
    assert tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"] == "0.2.9"
    assert json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"] == "0.2.9"


def test_ai_status_cli(home, tg, monkeypatch, tmp_path):
    code, out, _ = tg("ai-status", "--json")
    data = json.loads(out)
    assert code == 0 and data["choice"] == "auto" and data["active"] in ("claude", "off")
    assert data["gemini"]["state"] in ("no_node", "missing") and "fix" in data["gemini"]
    assert data["claude"]["state"] in ("missing", "not_logged_in", "connected", "unknown")
    code, out, _ = tg("ai-status")
    assert code == 0 and "AI answers in the window" in out and "Gemini:" in out
    code, out, _ = tg("claude-status", "--json")  # still there
    assert code == 0 and "state" in json.loads(out)
    # read-only sessions may run it: it is in both allow lists and not a write
    monkeypatch.setenv("TGKIT_READONLY", "1")
    code, out, _ = tg("ai-status", "--json")
    assert code == 0
    assert "tgkit ai-status *" in ai.READ_COMMANDS and "tgkit ai-status" in ai.READ_PREFIXES


def test_ai_status_with_gemini_signed_in(home, tg, monkeypatch, tmp_path):
    ghome = tmp_path / "g"
    (ghome / ".gemini").mkdir(parents=True)
    (ghome / ".gemini" / "settings.json").write_text('{"security":{"auth":{"selectedType":"oauth-personal"}}}',
                                                     encoding="utf-8")
    (ghome / ".gemini" / "google_accounts.json").write_text('{"active":"lan@example.com"}', encoding="utf-8")
    monkeypatch.setenv("GEMINI_CLI_HOME", str(ghome))
    monkeypatch.setenv("TGKIT_GEMINI", STUB)
    monkeypatch.setenv("TGKIT_CLAUDE", "off")
    code, out, _ = tg("ai-status", "--json")
    data = json.loads(out)
    assert data["active"] == "gemini" and data["gemini"]["state"] == "connected"
    assert data["gemini"]["account"] == "lan@example.com"
    code, out, _ = tg("ai-status")
    assert "Gemini: signed in as lan@example.com" in out


def test_api_ai_status_settings_and_setup_need_the_token(server, monkeypatch):
    app, base = server
    for path in ("/api/ai-status", "/api/gemini/install", "/api/gemini/login", "/api/gemini/node"):
        body = None if path == "/api/ai-status" else {}
        assert call(base, path, body, token=None)[0] in (401, 403)
        assert call(base, path, body, token="wrong")[0] in (401, 403)
    code, st = call(base, "/api/ai-status?refresh=1")
    assert code == 200 and st["choice"] == "auto" and set(st) >= {"active", "claude", "gemini"}
    code, s = call(base, "/api/settings", {"ai_provider": "gemini"})
    assert code == 200 and s["ai_provider"] == "gemini"
    assert call(base, "/api/state")[1]["ai"] == "gemini"
    code, err = call(base, "/api/settings", {"ai_provider": "gpt"})
    assert err.get("type") == "error" and "Claude, Gemini or Off" in err["message"]
    code, s = call(base, "/api/settings", {"ai_provider": "off"})
    assert call(base, "/api/state")[1]["ai"] is None
    code, res = call(base, "/api/gemini/login", {})
    assert code == 200 and res["ok"] and res["argv"] == [sys.executable, STUB]
    monkeypatch.setenv("TGKIT_NPM", "off")
    monkeypatch.setenv("TGKIT_NODE", "off")
    code, res = call(base, "/api/gemini/install", {})
    assert code == 200 and res["ok"] is False and res["hint"] == "Press Install Node.js first."


def test_window_strings_in_both_languages():
    js = (WEB / "static" / "app.js").read_text(encoding="utf-8")
    html = (WEB / "index.html").read_text(encoding="utf-8")
    keys = ("secAi", "aiOff", "aiAutoPick", "aiHelp", "aiAuto", "aiNone", "geminiWhat", "gStNoNode", "gStOldNode", "gStMissing",
            "gStNotLogged", "gStConnected", "gFixNoNode", "gFixMissing", "gFixNotLogged", "installNode",
            "installGemini", "loginGemini", "installingGemini", "geminiInstalled", "nodeOpened", "nodeSite",
            "modeGemini", "geminiOn", "noAi")
    for k in keys:
        assert len(re.findall(rf"\b{k}: ", js)) == 2, k  # once in en, once in vi
    for ident in ("geminiDot", "geminiStateText", "geminiSteps", "geminiNode", "geminiInstall", "geminiLogin",
                  "geminiCheck", "aiNote"):
        assert f'id="{ident}"' in html and f'$("{ident}")' in js
    for value in ("auto", "claude", "gemini", "off"):  # auto: the way back after picking one
        assert f'name="ai" value="{value}"' in html
    assert "/api/ai-status" in js and "/api/gemini/install" in js and "/api/gemini/login" in js


def test_readme_and_no_dashes():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "## Using Gemini instead of Claude" in readme and "tgkit ai-status" in readme
    for rel in ("README.md", "docs/MAINTENANCE.md", "src/tgkit/ai.py", "src/tgkit/gemini.py", "src/tgkit/playbook.md",
                "src/tgkit/appserver.py", "src/tgkit/web/static/app.js", "src/tgkit/web/index.html",
                "src/tgkit/errors.py", "tests/fake_gemini.py", "tests/test_gemini_mode.py", "tests/test_v029.py"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert chr(0x2014) not in text and chr(0x2013) not in text, rel
