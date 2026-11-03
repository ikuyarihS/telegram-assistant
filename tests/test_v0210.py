"""0.2.10: Check for updates and Update from the GitHub releases, against a fake GitHub (no network)."""

import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from tgkit import __version__, ai, claude, gemini, mcp, updater
from tgkit.appserver import App, serve
from tgkit.claude import ClaudeRunner
from tgkit.errors import UserError
from tgkit.gemini import GeminiRunner

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "src" / "tgkit" / "web"
TOKEN = "tok-0210"
NEXT = "0.2.11"

# The zip's own installer: it only ever runs through the helper, after the checks.
INSTALL_STUB = ("$Kit = Split-Path -Parent $PSScriptRoot\n"
                "[IO.File]::WriteAllText((Join-Path $Kit 'installed.txt'), 'ok')\n"
                "exit $(if (Test-Path (Join-Path $Kit 'fail-install')) { 1 } else { 0 })\n")


def make_zip(version: str, pyver: str | None = None, extra: dict | None = None) -> bytes:
    buf = io.BytesIO()
    p = "Telegram Assistant/"
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(p + "pyproject.toml", f'[project]\nname = "tgkit"\nversion = "{pyver or version}"\n')
        zf.writestr(p + "install.sh", "#!/bin/sh\ntouch \"$(dirname \"$0\")/installed.txt\"\n")
        zf.writestr(p + "scripts/install.ps1", INSTALL_STUB)
        zf.writestr(p + "src/tgkit/__init__.py", f'__version__ = "{version}"\n')
        zf.writestr(p + "README.md", f"Telegram Assistant {version}\n")
        for name, data in (extra or {}).items():
            zf.writestr(name, data)
    return buf.getvalue()


class FakeGitHub:
    """api.github.com's releases/latest, github.com's asset link (a redirect) and the download host."""

    def __init__(self):
        self.release = None  # None: 404, like a repo with no release yet
        self.status = 200
        self.files: dict[str, bytes] = {}
        self.redirect_base = None  # where /dl/ redirects to (default: this server)
        self.hits: list[str] = []
        gh = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, data=b"", ctype="application/octet-stream", extra=None):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                for k, v in (extra or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                gh.hits.append(self.path)
                if self.path == "/repos/ikuyarihS/telegram-assistant/releases/latest":
                    if gh.status != 200 or gh.release is None:
                        return self._send(gh.status if gh.status != 200 else 404, b'{"message":"no"}')
                    return self._send(200, json.dumps(gh.release).encode(), "application/json")
                if self.path.startswith("/dl/"):
                    name = self.path[4:]
                    return self._send(302, extra={"Location": f"{gh.redirect_base or gh.base}/obj/{name}"})
                if self.path.startswith("/obj/") and self.path[5:] in gh.files:
                    return self._send(200, gh.files[self.path[5:]])
                return self._send(404)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def publish(self, version, zip_bytes=None, sha=None, with_zip=True, with_sha=True,
                body="## What is new\n\n* **Faster** search\n* See [the notes](https://example.org/x)\n"):
        zname = f"TelegramAssistant-{version}.zip"
        zip_bytes = make_zip(version) if zip_bytes is None else zip_bytes
        sha = f"{hashlib.sha256(zip_bytes).hexdigest()}  {zname}\n" if sha is None else sha
        assets = []
        if with_zip:
            self.files[zname] = zip_bytes
            assets.append({"name": zname, "browser_download_url": f"{self.base}/dl/{zname}"})
        if with_sha:
            self.files[zname + ".sha256"] = sha.encode()
            assets.append({"name": zname + ".sha256", "browser_download_url": f"{self.base}/dl/{zname}.sha256"})
        self.release = {"tag_name": f"v{version}", "body": body, "assets": assets,
                        "html_url": f"{self.base}/releases/v{version}"}


@pytest.fixture
def gh(monkeypatch):
    fake = FakeGitHub()
    monkeypatch.setattr(updater, "API", f"{fake.base}/repos/ikuyarihS/telegram-assistant/releases/latest")
    monkeypatch.setattr(updater, "HOSTS", ("127.0.0.1",))
    monkeypatch.setattr(updater, "SCHEMES", ("http",))
    yield fake
    fake.httpd.shutdown()


@pytest.fixture
def kit(tmp_path):
    """An installed Telegram Assistant folder: the code, its .venv and nothing of the user's data."""
    k = tmp_path / "Telegram Assistant"
    (k / "scripts").mkdir(parents=True)
    (k / ".venv").mkdir()
    (k / ".venv" / "keep.txt").write_text("old venv", encoding="utf-8")
    (k / "pyproject.toml").write_text(f'version = "{__version__}"\n', encoding="utf-8")
    (k / "install.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (k / "scripts" / "install.ps1").write_text("exit 0\n", encoding="utf-8")
    (k / "README.md").write_text(f"Telegram Assistant {__version__}\n", encoding="utf-8")
    return k


def refused(fn, *a) -> str:
    with pytest.raises(UserError) as exc:
        fn(*a)
    return exc.value.message


# ---------------------------------------------------------------- the check

def test_version_is_0210_everywhere():
    import tomllib
    assert __version__ == "0.2.10"
    assert tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"] == "0.2.10"
    assert json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"] == "0.2.10"


def test_versions_compare_as_numbers():
    assert updater.parse("v0.2.10") == (0, 2, 10) > updater.parse("v0.2.9")
    assert updater.parse("0.3.0") and updater.parse("v1.0") is None and updater.parse("v0.2.10-beta") is None
    assert updater.is_newer("0.2.11") and updater.is_newer("0.3.0")
    assert not updater.is_newer("0.2.10") and not updater.is_newer("0.2.9") and not updater.is_newer("")


def test_check_newer_same_and_older(home, gh):
    gh.publish(NEXT)
    res = updater.check(home)
    assert res["ok"] and res["newer"] and res["latest"] == NEXT and res["current"] == __version__
    assert res["notes"] == "What is new\n\n- Faster search\n- See the notes"  # plain words
    for old in (__version__, "0.2.9", "0.1.0"):
        gh.publish(old)
        res = updater.check(home)
        assert res["ok"] and not res["newer"] and res["latest"] == old, old
    gh.release = {**gh.release, "tag_name": "nightly"}
    assert not updater.check(home)["newer"]


def test_offline_and_rate_limited_are_silent(home, gh, monkeypatch):
    for status in (403, 429, 500, 404):
        gh.status = status
        res = updater.check(home)
        assert res == {**res, "ok": False, "offline": True, "newer": False}, status
    gh.status = 200
    gh.release = None  # no release yet
    assert updater.check(home)["offline"]
    monkeypatch.setattr(updater, "API", "http://127.0.0.1:1/closed")  # nothing listens there
    assert updater.check(home)["offline"]
    monkeypatch.setattr(updater, "API", "https://evil.example/releases/latest")  # not a GitHub host
    hits = len(gh.hits)
    assert updater.check(home)["offline"] and len(gh.hits) == hits


def test_quiet_check_once_a_day_and_it_can_be_turned_off(home, gh):
    gh.publish(NEXT)
    now = time.time()
    first = updater.check(home, quiet=True, now=now)
    assert first["newer"] and first["latest"] == NEXT and len(gh.hits) == 1
    again = updater.check(home, quiet=True, now=now + 3600)
    assert again["cached"] and again["newer"] and again["latest"] == NEXT and len(gh.hits) == 1
    updater.check(home, quiet=True, now=now + updater.DAY + 1)
    assert len(gh.hits) == 2
    gh.status = 503  # offline counts as the day's check too: no retry at every open
    assert not updater.check(home, quiet=True, now=now + 3 * updater.DAY)["ok"]
    assert updater.check(home, quiet=True, now=now + 3 * updater.DAY + 60)["cached"] and len(gh.hits) == 3
    app = App(home, claude=ClaudeRunner(cmd=[]), gemini=GeminiRunner(cmd=[]), token=TOKEN)
    assert app.settings()["update_check"] is True
    assert app.save_settings({"update_check": False})["update_check"] is False
    off = updater.check(home, quiet=True, now=now + 9 * updater.DAY)
    assert off["skipped"] and not off["newer"] and len(gh.hits) == 3
    assert updater.check(home)["ok"] is False and len(gh.hits) == 4  # Check for updates still asks
    assert json.loads((home / "config.json").read_text(encoding="utf-8"))["update_check"] is False


# ---------------------------------------------------------------- download, checksum, staging

def test_a_good_update_is_staged_and_nothing_ran(home, gh, kit):
    gh.publish(NEXT)
    new = updater.download(updater.latest(), kit)
    assert new == kit / ".update" / "new" / "Telegram Assistant"
    assert (new / "pyproject.toml").read_text(encoding="utf-8").count(NEXT) == 1
    assert not list((kit / ".update").glob("*.zip"))  # the zip is gone once unzipped
    assert not (kit / "installed.txt").exists() and not (new / "installed.txt").exists()
    assert (kit / "README.md").read_text(encoding="utf-8") == f"Telegram Assistant {__version__}\n"  # not yet
    assert ["/dl/TelegramAssistant-0.2.11.zip.sha256", "/obj/TelegramAssistant-0.2.11.zip.sha256",
            "/dl/TelegramAssistant-0.2.11.zip", "/obj/TelegramAssistant-0.2.11.zip"] == gh.hits[1:]
    res = updater.start(kit, new, pid=12345)  # TGKIT_NO_REVEAL: says what would run
    script = kit / ".update" / ("apply.ps1" if os.name == "nt" else "apply.sh")
    assert res["ok"] and str(script) in res["argv"] and "12345" in res["argv"] and str(new) in res["argv"]
    assert script.read_text(encoding="utf-8") == (updater.APPLY_PS1 if os.name == "nt" else updater.APPLY_SH)
    for text in (updater.APPLY_PS1, updater.APPLY_SH):  # the helper is ours, and it never touches .venv or data
        assert "install" in text and ".update" in text and "TGKIT_HOME" not in text
    assert "/XD .venv bin .git .update" in updater.APPLY_PS1 and "-Update" in updater.APPLY_PS1


def test_a_bad_hash_is_refused_and_nothing_is_kept(home, gh, kit):
    gh.publish(NEXT, sha=f"{'0' * 64}  TelegramAssistant-{NEXT}.zip\n")
    assert "does not match its checksum" in refused(updater.download, updater.latest(), kit)
    assert not (kit / ".update").exists() and not (kit / "installed.txt").exists()
    other = make_zip(NEXT, extra={"Telegram Assistant/evil.txt": "x"})  # a different zip with the right name
    gh.publish(NEXT, sha=f"{hashlib.sha256(make_zip(NEXT)).hexdigest()}  TelegramAssistant-{NEXT}.zip\n",
               zip_bytes=other)
    assert "does not match its checksum" in refused(updater.download, updater.latest(), kit)
    assert not (kit / ".update").exists()
    for junk in ("", "not a hash\n", f"{'a' * 63}\n", f"{'a' * 64}  TelegramAssistant-9.9.9.zip\n"):
        gh.publish(NEXT, sha=junk)
        assert "no usable checksum" in refused(updater.download, updater.latest(), kit), junk
        assert not (kit / ".update").exists()
    gh.publish(NEXT, sha=hashlib.sha256(make_zip(NEXT)).hexdigest().upper() + "\n")  # the hash alone is fine
    assert updater.download(updater.latest(), kit).is_dir()


def test_a_missing_asset_is_refused(home, gh, kit):
    gh.publish(NEXT, with_zip=False)
    assert "no Telegram Assistant zip" in refused(updater.download, updater.latest(), kit)
    gh.files.clear()
    gh.publish(NEXT, with_sha=False)
    assert "no checksum file" in refused(updater.download, updater.latest(), kit)
    assert not (kit / ".update").exists()
    gh.publish(NEXT)
    del gh.files[f"TelegramAssistant-{NEXT}.zip"]  # listed, but the download host has nothing
    assert "Could not download" in refused(updater.download, updater.latest(), kit)
    assert not (kit / ".update").exists()


def test_a_redirect_away_from_github_is_refused(home, gh, kit):
    gh.publish(NEXT)
    gh.redirect_base = f"http://localhost:{gh.port}"  # the same bytes, but not an allowed host
    assert "not GitHub" in refused(updater.download, updater.latest(), kit)
    assert not (kit / ".update").exists()
    assert not any(h.startswith("/obj/") for h in gh.hits)  # the other host was never asked
    info = updater.latest()
    gh.redirect_base = None
    info["assets"][f"TelegramAssistant-{NEXT}.zip"] = f"http://localhost:{gh.port}/obj/TelegramAssistant-{NEXT}.zip"
    assert "not GitHub" in refused(updater.download, info, kit)
    assert not updater.allowed("https://github.com.evil.example/x") and not updater.allowed("ftp://github.com/x")
    assert not updater.allowed("http://user:pw@127.0.0.1/x")


def test_https_only_and_github_hosts_by_default():
    assert updater.SCHEMES == ("https",)
    assert set(updater.HOSTS) == {"api.github.com", "github.com", "objects.githubusercontent.com",
                                  "release-assets.githubusercontent.com"}
    assert updater.API == "https://127.0.0.1:9/no-network-in-tests"  # conftest: no test reaches GitHub
    src = (ROOT / "src" / "tgkit" / "updater.py").read_text(encoding="utf-8")
    assert 'REPO = "ikuyarihS/telegram-assistant"' in src
    assert 'API = f"https://api.github.com/repos/{REPO}/releases/latest"' in src and "Authorization" not in src
    assert not updater.allowed("http://github.com/x") and updater.allowed("https://github.com/x")


def test_never_a_downgrade_or_the_same_version(home, gh, kit):
    for version in (__version__, "0.2.9", "0.1.0", ""):
        gh.publish(version or "0.0.0")
        info = {**updater.latest(), "version": version}
        hits = len(gh.hits)
        assert "already the newest" in refused(updater.download, info, kit), version
        assert len(gh.hits) == hits  # nothing was downloaded
    gh.publish(NEXT, zip_bytes=make_zip(NEXT, pyver="0.2.1"))  # the tag says newer, the zip is older
    assert "is not the Telegram Assistant it says" in refused(updater.download, updater.latest(), kit)
    assert not (kit / ".update").exists()


@pytest.mark.parametrize("bad", ["../evil.txt", "Telegram Assistant/../../evil.txt", "/abs/evil.txt",
                                 "C:/evil.txt", "Telegram Assistant\\..\\evil.txt"])
def test_a_zip_with_a_path_outside_is_refused(home, gh, kit, bad):
    gh.publish(NEXT, zip_bytes=make_zip(NEXT, extra={bad: "x"}))
    assert "outside its folder" in refused(updater.download, updater.latest(), kit)
    assert not (kit / ".update").exists() and not (kit / "evil.txt").exists() and not (kit.parent / "evil.txt").exists()


def test_staging_is_cleaned_before_and_after(home, gh, kit):
    (kit / ".update" / "new" / "old").mkdir(parents=True)
    (kit / ".update" / "new" / "old" / "left.txt").write_text("from an earlier try", encoding="utf-8")
    gh.publish(NEXT)
    new = updater.download(updater.latest(), kit)
    assert not (kit / ".update" / "new" / "old").exists() and new.is_dir()
    gh.publish(NEXT, sha=f"{'1' * 64}\n")
    refused(updater.download, updater.latest(), kit)
    assert not (kit / ".update").exists()
    assert (kit / ".venv" / "keep.txt").read_text(encoding="utf-8") == "old venv"  # never touched


def test_kit_dir_is_only_an_installed_folder(kit, monkeypatch):
    monkeypatch.setattr(sys, "prefix", str(kit / ".venv"))
    assert updater.kit_dir() == kit
    (kit / ".git").mkdir()  # a git copy updates with git
    assert updater.kit_dir() is None
    monkeypatch.setattr(sys, "prefix", str(kit.parent / "some-python"))
    assert updater.kit_dir() is None


@pytest.mark.skipif(os.name != "nt", reason="the Windows helper runs PowerShell and robocopy")
@pytest.mark.parametrize("install_ok", [True, False])
def test_the_helper_updates_the_folder_after_the_app_ends(home, gh, kit, tmp_path, install_ok):
    """The real helper: it waits for the app's pid, copies the new files (never .venv), runs
    the new install.ps1 -Update. A failed install leaves the old .venv as it was."""
    gh.publish(NEXT)
    new = updater.download(updater.latest(), kit)
    if not install_ok:
        (kit / "fail-install").write_text("", encoding="utf-8")
    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait()
    argv = updater.helper_argv(kit, new, gone.pid)
    data_before = sorted(p.relative_to(home).as_posix() for p in home.rglob("*"))
    r = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120)
    assert r.returncode == (0 if install_ok else 1), r.stdout + r.stderr
    assert (kit / "README.md").read_text(encoding="utf-8") == f"Telegram Assistant {NEXT}\n"
    assert (kit / "installed.txt").read_text(encoding="utf-8") == "ok"
    assert (kit / ".venv" / "keep.txt").read_text(encoding="utf-8") == "old venv"
    assert not (kit / ".update" / "new").exists()
    assert sorted(p.relative_to(home).as_posix() for p in home.rglob("*")) == data_before  # data untouched
    if not install_ok:
        assert "old version still works" in r.stdout


# ---------------------------------------------------------------- the window

def call(base, path, body=None, lang="en"):
    req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode("utf-8"),
                                 method="GET" if body is None else "POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-TGKit-Token", TOKEN)
    req.add_header("X-TGKit-Lang", lang)
    with urllib.request.urlopen(req, timeout=30) as res:
        return json.loads(res.read().decode("utf-8"))


@pytest.fixture
def window(home):
    app = App(home, claude=ClaudeRunner(cmd=[]), gemini=GeminiRunner(cmd=[]), token=TOKEN)
    httpd = serve(app)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield app, f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def test_window_check_and_update(window, gh, kit, monkeypatch):
    app, base = window
    assert call(base, "/api/update/check?quiet=1")["ok"] is False  # no release yet: quiet, no error
    assert call(base, "/api/update/check?quiet=1")["cached"] and len(gh.hits) == 1
    gh.publish(NEXT)
    res = call(base, "/api/update/check")
    assert res["newer"] and res["latest"] == NEXT and res["notes"]
    monkeypatch.setattr(updater, "kit_dir", lambda: None)
    res = call(base, "/api/update/start", {})
    assert res["user_error"] and "cannot update itself" in res["message"]
    res = call(base, "/api/update/start", {}, lang="vi")
    assert res["message"] == "Bản Telegram Assistant này không tự cập nhật được."
    monkeypatch.setattr(updater, "kit_dir", lambda: kit)
    closed = threading.Event()
    app.quit_hook = closed.set
    res = call(base, "/api/update/start", {})
    assert res["ok"] and res["closing"] and res["version"] == NEXT and res["argv"]
    assert closed.wait(10)  # the window closes; the helper finishes the update after it
    gh.publish(NEXT, sha=f"{'2' * 64}\n")
    res = call(base, "/api/update/start", {}, lang="vi")
    assert res["user_error"] and res["message"] == "Bản cập nhật tải về không khớp với mã kiểm tra, nên đã không dùng nó."
    assert not (kit / ".update").exists()
    assert call(base, "/api/settings", {"update_check": False})["update_check"] is False
    assert call(base, "/api/update/check?quiet=1")["skipped"]


def test_window_strings_and_controls():
    js = (WEB / "static" / "app.js").read_text(encoding="utf-8")
    html = (WEB / "index.html").read_text(encoding="utf-8")
    for k in ("secUpdate", "updateThis", "updateCheck", "updateNewest", "updateOffline", "updateReady", "updateGo",
              "updateKept", "updateWorking", "updateClosing", "updateCloseIt", "updateAuto"):
        assert len(re.findall(rf"\b{k}: (?!\$\()", js)) == 2, k  # once in en, once in vi (not the ui map)
    for ident in ("updateBanner", "updateBannerText", "updateBannerGo", "updateSec", "updateThis", "updateCheck",
                  "updateNote", "updateBox", "updateTitle", "updateNotes", "updateGo", "updateAuto"):
        assert f'id="{ident}"' in html and f'$("{ident}")' in js, ident
    assert "/api/update/check?quiet=1" in js and "/api/update/start" in js and "update_check:" in js
    assert "Version ${v} is ready" in js and "Check for updates" in html
    assert "innerHTML" not in js.split("Check for updates (0.2.10)")[1].split("Browser mode")[0]  # notes as text


def test_every_update_message_has_vietnamese():
    from tgkit.errors import VI
    src = (ROOT / "src" / "tgkit" / "updater.py").read_text(encoding="utf-8")
    msgs = re.findall(r'UserError\(\s*"([^"]+)"', src) + re.findall(r'UserError\([^)]*?,\s*"([^"]+)"\)', src)
    assert len(msgs) >= 10
    for m in [*msgs, updater.NOT_USED, updater.not_installed().hint]:
        assert m in VI, m


# ---------------------------------------------------------------- the CLI and the AI

def test_cli_update_check(home, gh, tg):
    code, out, _ = tg("update", "--check")
    assert code == 1 and "Could not check for updates" in out
    gh.publish(NEXT)
    code, out, _ = tg("update", "--check")
    assert code == 0 and f"Version {NEXT} is ready" in out and "tgkit update" in out and "Faster search" in out
    code, out, _ = tg("update", "--check", "--json")
    assert code == 0 and json.loads(out)["latest"] == NEXT
    gh.publish(__version__)
    code, out, _ = tg("update", "--check")
    assert code == 0 and "is the newest" in out
    code, _, err = tg("update")  # the test Python is not an installed Telegram Assistant folder
    assert code == 1 and "cannot update itself" in err


def test_cli_update_downloads_and_hands_over(home, gh, tg, kit, monkeypatch):
    monkeypatch.setattr(updater, "kit_dir", lambda: kit)
    gh.publish(__version__)
    code, out, _ = tg("update")
    assert code == 0 and "Nothing to update" in out and not (kit / ".update").exists()
    gh.publish(NEXT, sha=f"{'3' * 64}\n")
    code, _, err = tg("update")
    assert code == 1 and "does not match its checksum" in err and not (kit / ".update").exists()
    gh.publish(NEXT)
    code, out, _ = tg("update")
    assert code == 0 and "matches its checksum" in out and "new window" in out
    assert (kit / ".update" / "new" / "Telegram Assistant" / "pyproject.toml").exists()


def test_sync_is_no_longer_called_update(tg, home):
    from tgkit import cli
    subs = next(a for a in cli.build_parser()._actions if a.__class__.__name__ == "_SubParsersAction").choices
    assert subs["update"].get_default("func") is cli.cmd_update
    assert subs["upgrade"].get_default("func") is cli.cmd_update
    assert subs["sync"].get_default("func") is cli.cmd_sync
    code, out, _ = tg("help")
    assert "tgkit update --check" in out and "tgkit sync" in out


def test_the_ai_can_never_update(home, tg, gh, monkeypatch, kit):
    gh.publish(NEXT)
    monkeypatch.setattr(updater, "kit_dir", lambda: kit)
    monkeypatch.setenv("TGKIT_READONLY", "1")
    for args in (("update",), ("update", "--check"), ("upgrade",)):
        code, _, err = tg(*args)
        assert code == 1 and "read-only" in err, args
    assert gh.hits == [] and not (kit / ".update").exists()
    for cmd in ("tgkit update", "tgkit update --check", "tgkit upgrade", 'tgkit "update"'):
        assert not ai.allowed_command(cmd), cmd
    assert "update" in ai.WRITE_COMMANDS and "upgrade" in ai.WRITE_COMMANDS
    assert not any(c in ("update", "upgrade") for c, *_ in mcp.TOOLS.values())
    assert not any("update" in t or "upgrade" in t for t in claude.ALLOWED)
    assert not any("update" in r for r in ai.READ_COMMANDS)
    assert "update" not in gemini.policy_text() and "upgrade" not in gemini.policy_text()
    with pytest.raises(mcp.ToolError):
        mcp.argv("update", {})


# ---------------------------------------------------------------- release scripts and docs

def test_share_zip_writes_the_checksum_and_release_uses_gh():
    share = (ROOT / "scripts" / "make-share-zip.ps1").read_text(encoding="utf-8")
    assert "Get-FileHash -Algorithm SHA256" in share and '"$Zip.sha256"' in share
    assert "$Hash  TelegramAssistant-$Version.zip" in share
    rel = (ROOT / "scripts" / "release.ps1").read_text(encoding="utf-8")
    assert "gh @ghArgs" in rel and "'release', 'create', $Tag, $Zip, $Sum" in rel
    assert "ikuyarihS/telegram-assistant" in rel and "git push" not in rel
    assert "is not pushed yet" in rel and "does not match the zip" in rel
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "Check for" in readme and "tgkit update --check" in readme and "scripts\\release.ps1" in readme
    assert ".update/" in (ROOT / ".gitignore").read_text(encoding="utf-8")


def test_the_sha256_line_matches_what_the_updater_reads(tmp_path):
    """The line make-share-zip.ps1 writes ("hash  name") is one the updater accepts."""
    data = make_zip(NEXT)
    line = f"{hashlib.sha256(data).hexdigest()}  TelegramAssistant-{NEXT}.zip\n"
    m = re.fullmatch(r"([0-9a-fA-F]{64})(?:[ \t]+\*?([^\r\n]+))?", line.splitlines()[0].strip())
    assert m and m[2] == f"TelegramAssistant-{NEXT}.zip"


def test_no_dashes_in_the_new_files():
    for rel in ("src/tgkit/updater.py", "tests/test_v0210.py", "scripts/release.ps1", "scripts/make-share-zip.ps1",
                "README.md", "src/tgkit/cli.py", "src/tgkit/app.py", "src/tgkit/web/static/app.css"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert chr(0x2014) not in text and chr(0x2013) not in text, rel
