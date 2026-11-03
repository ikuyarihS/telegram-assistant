"""Check for updates and update from the GitHub releases (0.2.10).

The check reads the public releases API (no login, no token) and compares the tag vX.Y.Z
with this version. An update downloads TelegramAssistant-X.Y.Z.zip and its .sha256 from
the same release, over https and only from GitHub hosts (a redirect anywhere else is
refused), checks the zip against the checksum before anything else, unzips it into the
kit folder's .update folder (refusing a path that leaves it) and checks it is that
version. Only then a small helper written by this module (not taken from the zip) waits
for this process to end, copies the new files over the kit folder (never .venv, bin or
.git, and never the data folder) and runs the same installer as Update-Windows.bat
(install.ps1 -Update; install.sh on a Mac or Linux). When that installer fails, the old
version in .venv keeps working. Never a downgrade: only a newer version is downloaded.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

from . import __version__, config
from .errors import UserError
from .store import atomic_write, read_json

REPO = "ikuyarihS/telegram-assistant"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
PAGE = f"https://github.com/{REPO}/releases/latest"
SCHEMES = ("https",)
# github.com serves the asset link and redirects to its download host (both names are in use).
HOSTS = ("api.github.com", "github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com")
TIMEOUT = 10
DAY = 24 * 3600
MAX_SMALL = 1 << 20  # the API answer and the .sha256 file
MAX_ZIP = 100 << 20
MAX_UNZIPPED = 400 << 20
STAGING = ".update"
SETTING = "update_check"
NEW_CONSOLE = 0x00000010  # CREATE_NEW_CONSOLE
BREAKAWAY = 0x01000000  # CREATE_BREAKAWAY_FROM_JOB: outlive the app's launcher

NOT_USED = "Nothing was changed. Try again later, or ask whoever gave you Telegram Assistant."


class Offline(Exception):
    """GitHub could not be reached, or it said no (rate limit, no release yet)."""


def parse(tag) -> tuple[int, int, int] | None:
    m = re.fullmatch(r"v?(\d{1,4})\.(\d{1,4})\.(\d{1,4})", str(tag or "").strip())
    return (int(m[1]), int(m[2]), int(m[3])) if m else None


def is_newer(version: str) -> bool:
    new, now = parse(version), parse(__version__)
    return bool(new and now and new > now)


def allowed(url: str) -> bool:
    u = urlparse(str(url or ""))
    return u.scheme in SCHEMES and (u.hostname or "").lower() in HOSTS and not u.username and not u.password


def _not_github() -> UserError:
    return UserError("The download went to a place that is not GitHub, so it was stopped.", NOT_USED)


class _Redirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not allowed(newurl):
            raise _not_github()
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open(url: str, accept: str = "*/*", timeout: float = TIMEOUT):
    if not allowed(url):
        raise _not_github()
    req = urllib.request.Request(url, headers={"User-Agent": f"TelegramAssistant/{__version__}", "Accept": accept})
    res = urllib.request.build_opener(_Redirects).open(req, timeout=timeout)
    if not allowed(res.geturl()):
        res.close()
        raise _not_github()
    return res


def _small(url: str, accept: str = "*/*", timeout: float = TIMEOUT) -> bytes:
    try:
        with _open(url, accept, timeout) as res:
            data = res.read(MAX_SMALL + 1)
    except (urllib.error.URLError, OSError, ValueError) as exc:  # HTTPError (403, 404, 429) too
        raise Offline(str(exc)) from None
    if len(data) > MAX_SMALL:
        raise Offline("answer too big")
    return data


def plain(text, cap: int = 1500) -> str:
    """Release notes in plain words: no Markdown marks, links as their text."""
    s = str(text or "").replace("\r\n", "\n")
    s = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", s)
    s = re.sub(r"<[^>]*>", "", s)
    s = re.sub(r"^[ \t]{0,3}#{1,6}[ \t]*", "", s, flags=re.M)
    s = re.sub(r"^([ \t]*)[*+][ \t]+", r"\1- ", s, flags=re.M)
    s = re.sub(r"\*\*|__|`|(?<!\w)\*(?!\s)|(?<!\s)\*(?!\w)", "", s)
    s = re.sub(r"\n{3,}", "\n\n", s).strip()
    return s if len(s) <= cap else s[:cap].rstrip() + " ..."


def latest(timeout: float = TIMEOUT) -> dict:
    """The newest release on GitHub. Raises Offline when it cannot be read."""
    try:
        data = json.loads(_small(API, "application/vnd.github+json", timeout).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise Offline("not JSON") from None
    if not isinstance(data, dict):
        raise Offline("not a release")
    tag = str(data.get("tag_name") or "")
    v = parse(tag)
    assets = {}
    for a in data.get("assets") or []:
        if isinstance(a, dict) and a.get("name") and a.get("browser_download_url"):
            assets[str(a["name"])] = str(a["browser_download_url"])
    return {"tag": tag, "version": ".".join(map(str, v)) if v else "", "notes": plain(data.get("body")),
            "assets": assets}


# ------------------------------------------------------------------ the check

def state_path(home: Path) -> Path:
    return Path(home) / "app" / "update.json"


def enabled(home: Path) -> bool:
    return config.load(Path(home)).get(SETTING, True) is not False


def check(home: Path, quiet: bool = False, now: float | None = None) -> dict:
    """Is a newer version out? quiet is the window's check on open: nothing when it is
    turned off in Settings, at most one question to GitHub a day, and offline is no error."""
    now = time.time() if now is None else now
    base = {"current": __version__, "enabled": enabled(home), "newer": False}
    if quiet:
        if not base["enabled"]:
            return {**base, "ok": True, "skipped": True}
        st = read_json(state_path(home), {})
        if isinstance(st, dict) and st.get("current") == __version__ and 0 <= now - float(st.get("checked") or 0) < DAY:
            return {**base, **(st.get("result") or {}), "cached": True}
    try:
        info = latest()
        res = {"ok": True, "latest": info["version"], "tag": info["tag"], "newer": is_newer(info["version"]),
               "notes": info["notes"]}
    except (Offline, UserError):
        res = {"ok": False, "offline": True}
    try:
        atomic_write(state_path(home), json.dumps({"checked": now, "current": __version__, "result": res}))
    except OSError:
        pass
    return {**base, **res}


# ------------------------------------------------------------------ download and stage

def kit_dir() -> Path | None:
    """The folder Telegram Assistant was installed from: install.ps1 and install.sh put
    .venv inside it. None for a git checkout or any other Python (they update their own way)."""
    venv = Path(sys.prefix)
    kit = venv.parent
    if venv.name != ".venv" or (kit / ".git").exists():
        return None
    need = ("pyproject.toml", "install.sh", "scripts/install.ps1")
    return kit if all((kit / n).is_file() for n in need) else None


def asset_names(version: str) -> tuple[str, str]:
    zname = f"TelegramAssistant-{version}.zip"
    return zname, zname + ".sha256"


def clean(kit: Path) -> None:
    """Remove what an earlier update left in the staging folder (only that folder)."""
    shutil.rmtree(Path(kit) / STAGING, ignore_errors=True)


def _expected(url: str, zname: str) -> str:
    text = _small(url).decode("utf-8", "replace").strip()
    m = re.fullmatch(r"([0-9a-fA-F]{64})(?:[ \t]+\*?([^\r\n]+))?", text.splitlines()[0].strip() if text else "")
    if not m or (m[2] and PurePosixPath(m[2].strip().replace("\\", "/")).name != zname):
        raise UserError("This update has no usable checksum, so its download cannot be checked and was not used.",
                        NOT_USED)
    return m[1].lower()


def _fetch(url: str, dest: Path) -> str:
    h = hashlib.sha256()
    size = 0
    try:
        with _open(url, timeout=60) as res, open(dest, "wb") as out:
            while chunk := res.read(1 << 16):
                size += len(chunk)
                if size > MAX_ZIP:
                    raise UserError("The update is much bigger than Telegram Assistant, so it was not used.", NOT_USED)
                h.update(chunk)
                out.write(chunk)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise Offline(str(exc)) from None
    return h.hexdigest()


def _unzip(zpath: Path, dest: Path) -> None:
    bad = UserError("The update zip holds a file outside its folder, so it was not used.", NOT_USED)
    root = dest.resolve()
    with zipfile.ZipFile(zpath) as zf:
        items = zf.infolist()
        if sum(i.file_size for i in items) > MAX_UNZIPPED:
            raise UserError("The update is much bigger than Telegram Assistant, so it was not used.", NOT_USED)
        for i in items:
            parts = PurePosixPath(i.filename).parts
            if (not parts or i.filename.startswith(("/", "\\")) or "\\" in i.filename or ".." in parts
                    or ":" in i.filename or (i.external_attr >> 16) & 0o170000 == 0o120000):
                raise bad
            if not (root / i.filename).resolve().is_relative_to(root):
                raise bad
        zf.extractall(dest)


def _tree(new: Path, version: str) -> Path:
    """The unzipped folder holds Telegram Assistant at that version (read, not run)."""
    subs = [p for p in new.iterdir() if p.is_dir()]
    root = new if (new / "pyproject.toml").is_file() else subs[0] if len(subs) == 1 else new
    wrong = UserError("The update zip is not the Telegram Assistant it says it is, so it was not used.", NOT_USED)
    if not all((root / n).is_file() for n in ("pyproject.toml", "install.sh", "scripts/install.ps1",
                                              "src/tgkit/__init__.py")):
        raise wrong
    try:  # the same line make-share-zip.ps1 reads (Python 3.10 has no tomllib)
        got = re.search(r'(?m)^version\s*=\s*"([^"]+)"', (root / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise wrong from None
    if not got or got[1] != version:
        raise wrong
    return root


def download(info: dict, kit: Path) -> Path:
    """Download the release zip, check it against its .sha256, unzip it into
    kit/.update/new. Returns the new Telegram Assistant folder; nothing in it has run.
    Any failure removes the staging folder and leaves everything else as it was."""
    version = str(info.get("version") or "")
    if not is_newer(version):  # never a downgrade, never the same version again
        raise UserError("This Telegram Assistant is already the newest.", "There is nothing to update.")
    zname, hname = asset_names(version)
    assets = info.get("assets") or {}
    if not assets.get(zname):
        raise UserError("This update has no Telegram Assistant zip to download.", NOT_USED)
    if not assets.get(hname):
        raise UserError("This update has no checksum file, so its download cannot be checked and was not used.",
                        NOT_USED)
    kit = Path(kit)
    clean(kit)
    stage = kit / STAGING
    stage.mkdir(parents=True)
    try:
        want = _expected(assets[hname], zname)
        zpath = stage / zname
        if _fetch(assets[zname], zpath) != want:
            raise UserError("The downloaded update does not match its checksum, so it was not used.", NOT_USED)
        _unzip(zpath, stage / "new")
        root = _tree(stage / "new", version)
        zpath.unlink()
        return root
    except Offline:
        clean(kit)
        raise UserError("Could not download the update: no internet, or GitHub is busy.", NOT_USED) from None
    except BaseException:
        clean(kit)
        raise


def newest_or_error() -> dict:
    try:
        return latest()
    except Offline:
        raise UserError("Could not check for updates: no internet, or GitHub is busy.", "Try again later.") from None


# ------------------------------------------------------------------ finish after the window closes

APPLY_PS1 = r"""# Written by Telegram Assistant to finish an update after its window closes.
param([int]$AppPid, [string]$New, [string]$Kit)
$ErrorActionPreference = 'Continue'
Write-Host 'Updating Telegram Assistant. Waiting for its window to close...'
try { Wait-Process -Id $AppPid -Timeout 300 -ErrorAction Stop } catch {}
Start-Sleep -Seconds 2
robocopy "$New" "$Kit" /E /XD .venv bin .git .update /R:2 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) {
    Write-Host 'The new files could not be copied. The old version still works; try Check for updates again later.'
    Read-Host 'Press Enter to close this window'
    exit 1
}
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Kit 'scripts\install.ps1') -Update
$code = $LASTEXITCODE
Remove-Item -Recurse -Force (Join-Path $Kit '.update\new') -ErrorAction SilentlyContinue
if ($code -ne 0) {
    Write-Host 'The update did not finish. The old version still works; try Check for updates again later.'
    Read-Host 'Press Enter to close this window'
    exit 1
}
$App = Join-Path $Kit '.venv\Scripts\tgkit-app.exe'
if (Test-Path $App) { Start-Process $App }
exit 0
"""

APPLY_SH = r"""#!/bin/sh
# Written by Telegram Assistant to finish an update after its window closes.
APP_PID="$1"; NEW="$2"; KIT="$3"
echo "Updating Telegram Assistant. Waiting for its window to close..."
n=0
while kill -0 "$APP_PID" 2>/dev/null && [ "$n" -lt 300 ]; do sleep 1; n=$((n + 1)); done
sleep 2
if ! cp -R "$NEW/." "$KIT/"; then
  echo "The new files could not be copied. The old version still works."
  exit 1
fi
if ! bash "$KIT/install.sh" </dev/null; then
  echo "The update did not finish. The old version still works."
  exit 1
fi
rm -rf "$KIT/.update/new"
if [ -x "$KIT/.venv/bin/tgkit" ]; then nohup "$KIT/.venv/bin/tgkit" app >/dev/null 2>&1 & fi
exit 0
"""


def helper_argv(kit: Path, new: Path, pid: int) -> list[str]:
    """Write the helper next to the staged files and return how to start it."""
    stage = Path(kit) / STAGING
    if os.name == "nt":
        script = stage / "apply.ps1"
        atomic_write(script, APPLY_PS1)
        return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
                "-AppPid", str(pid), "-New", str(new), "-Kit", str(kit)]
    script = stage / "apply.sh"
    atomic_write(script, APPLY_SH)
    return ["sh", str(script), str(pid), str(new), str(kit)]


def start(kit: Path, new: Path, pid: int | None = None) -> dict:
    """Start the helper; it waits for pid (this process) to end, then updates."""
    argv = helper_argv(Path(kit), Path(new), os.getpid() if pid is None else pid)
    if os.environ.get("TGKIT_NO_REVEAL"):  # tests: say what would run, run nothing
        return {"ok": True, "argv": argv}
    try:
        if os.name == "nt":
            try:
                subprocess.Popen(argv, creationflags=NEW_CONSOLE | BREAKAWAY, close_fds=True)
            except OSError:  # the launcher's job does not allow breaking away: it lets children live anyway
                subprocess.Popen(argv, creationflags=NEW_CONSOLE, close_fds=True)
        else:
            log = open(Path(kit) / STAGING / "update-log.txt", "ab")  # noqa: SIM115 (the helper owns it)
            subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    except OSError:
        clean(kit)
        raise UserError("The update could not be started.", NOT_USED) from None
    return {"ok": True}


def not_installed() -> UserError:
    return UserError("This copy of Telegram Assistant cannot update itself.",
                     "Download the new zip from GitHub, unzip it over the old folder and double-click "
                     "Update-Windows.bat (on a Mac: Install-Mac.command).")
