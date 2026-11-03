import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fixture_data  # noqa: E402

from tgkit import cli  # noqa: E402


@pytest.fixture(autouse=True)
def _no_github(monkeypatch):
    """No test asks the real GitHub: an address the updater refuses (tests/test_v0210.py has a fake one)."""
    from tgkit import updater
    monkeypatch.setattr(updater, "API", "https://127.0.0.1:9/no-network-in-tests")


@pytest.fixture
def home(tmp_path, monkeypatch):
    base = tmp_path / "data"
    fixture_data.build(base)
    monkeypatch.setenv("TGKIT_HOME", str(base))
    # Saved files go to a test folder, and no Explorer / browser window ever opens.
    monkeypatch.setenv("TGKIT_EXPORT_DIR", str(tmp_path / "exports"))
    monkeypatch.setenv("TGKIT_NO_REVEAL", "1")
    # A Gemini CLI on the test computer never answers a test (tests/fake_gemini.py does, when asked).
    monkeypatch.setenv("TGKIT_GEMINI", "off")
    for k in ("TG_API_ID", "TG_API_HASH", "TGKIT_API_ID", "TGKIT_API_HASH", "TGKIT_DEBUG", "TGKIT_CLAUDE_MODEL",
              "TGKIT_GEMINI_MODEL", "GEMINI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    return base


@pytest.fixture
def tg(capsys):
    """Run `tgkit <args>`; returns (exit code, stdout, stderr)."""
    def run(*args):
        code = cli.main(list(args))
        out = capsys.readouterr()
        return code, out.out, out.err
    return run


@pytest.fixture
def tg_json(tg):
    def run(*args):
        code, out, err = tg(*args, "--json")
        assert code == 0, err
        return json.loads(out)
    return run
