"""Where TG Kit keeps your data, and your Telegram app credentials.

The data folder is, in order:
  1. the TGKIT_HOME environment variable;
  2. the folder written in ~/.tgkit-home by `tgkit setup` when you picked one;
  3. ~/TGKit.
Nothing is hard-coded to a drive or a user name.
"""

import os
import re
import threading
from pathlib import Path

from .errors import UserError
from .store import SyncLock, atomic_write, read_json, write_json

POINTER_NAME = ".tgkit-home"


def pointer_file() -> Path:
    return Path.home() / POINTER_NAME


def default_home() -> Path:
    return Path.home() / "TGKit"


def home() -> Path:
    env = os.environ.get("TGKIT_HOME")
    if env:
        return Path(env).expanduser()
    try:
        picked = pointer_file().read_text(encoding="utf-8").strip()
    except OSError:
        picked = ""
    if picked:
        return Path(picked).expanduser()
    return default_home()


def home_source() -> str:
    if os.environ.get("TGKIT_HOME"):
        return "set by the TGKIT_HOME environment variable"
    if pointer_file().exists():
        return f"chosen during setup (saved in {pointer_file()})"
    return "the default"


def remember_home(path: Path) -> None:
    """Save the folder the user picked, so every later run finds it."""
    if Path(path).expanduser() == default_home():
        if pointer_file().exists():
            atomic_write(pointer_file(), str(default_home()) + "\n")
        return
    atomic_write(pointer_file(), str(path) + "\n")


def config_path(base: Path) -> Path:
    return base / "config.json"


def load(base: Path) -> dict:
    return read_json(config_path(base), {})


def save(base: Path, cfg: dict) -> None:
    """Write the whole file (atomic: tmp then replace). TG Kit itself changes
    config.json only through update() / put(), which hold the config lock."""
    write_json(config_path(base), cfg)


class ConfigLock(SyncLock):
    """One change of config.json at a time, across threads and processes: the
    app's Settings, a login finishing in the app, `tgkit setup` and log out each
    read the file, change some keys and write it back; without the lock the
    later write would drop the earlier change. Changes are short, so a second
    writer waits a little instead of refusing."""

    name = "config.lock"
    wait = 15.0

    def _busy(self) -> UserError:
        return UserError("The settings file is busy: another TG Kit is changing it.", "Try again in a moment.")


_CONFIG_MUTEX = threading.Lock()


def update(base: Path, change) -> dict:
    """Read config.json, let change(cfg) edit it in place, write it back, all
    under the config lock. A UserError raised by change leaves the file as it was."""
    base = Path(base)
    base.mkdir(parents=True, exist_ok=True)
    with _CONFIG_MUTEX, ConfigLock(base):
        cfg = load(base)
        change(cfg)
        save(base, cfg)
    return cfg


def put(base: Path, **values) -> dict:
    """Set some keys of config.json under the lock; a value None removes the key."""
    def change(cfg: dict) -> None:
        for key, value in values.items():
            if value is None:
                cfg.pop(key, None)
            else:
                cfg[key] = value
    return update(base, change)


def _env_file(base: Path) -> dict:
    """KEY=VALUE lines from <data>/.env, for people coming from the original tool."""
    path = base / ".env"
    out = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _setting(base: Path, cfg: dict, key: str, *env_names: str):
    for name in env_names:
        if os.environ.get(name):
            return os.environ[name]
    if cfg.get(key):
        return cfg[key]
    env = _env_file(base)
    for name in env_names:
        if env.get(name):
            return env[name]
    return None


def credentials(base: Path, cfg: dict) -> tuple[int, str]:
    api_id = _setting(base, cfg, "api_id", "TGKIT_API_ID", "TG_API_ID")
    api_hash = _setting(base, cfg, "api_hash", "TGKIT_API_HASH", "TG_API_HASH")
    if not api_id or not api_hash:
        raise UserError(
            "TG Kit is not connected to Telegram yet.",
            "Run: tgkit setup   (it walks you through it, about 3 minutes).",
        )
    if not valid_api_id(str(api_id)) or not valid_api_hash(str(api_hash)):
        raise UserError(
            "The saved Telegram api_id / api_hash look wrong.",
            "Run: tgkit setup   and type them again.",
        )
    return int(api_id), str(api_hash)


def has_credentials(base: Path, cfg: dict) -> bool:
    try:
        credentials(base, cfg)
        return True
    except UserError:
        return False


def max_media_bytes(base: Path, cfg: dict) -> int:
    raw = _setting(base, cfg, "max_media_mb", "TGKIT_MAX_MEDIA_MB", "TG_MAX_MEDIA_MB")
    try:
        mb = int(raw) if raw is not None else 50
    except ValueError:
        mb = 50
    return mb * 1024 * 1024


def valid_api_id(text: str) -> bool:
    return bool(re.fullmatch(r"\d{3,12}", text.strip()))


def valid_api_hash(text: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-fA-F]{32}", text.strip()))


def session_base(base: Path) -> Path:
    """Telethon adds '.session' to this name. The file is a logged-in key: never share it."""
    return base / "tgkit"


def logged_in(base: Path) -> bool:
    """Telethon creates the session file before any login, so the file alone
    proves nothing. setup saves "me" only after a login succeeds."""
    return (base / "tgkit.session").exists() and bool(load(base).get("me"))
