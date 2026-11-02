"""TG Kit's log file: <data folder>/app/logs/tgkit.log, rotated (1 MB, 3 old files kept).

Every caught error that is not a plain UserError is written here with its full
traceback, by the window, the setup in the window, a sync and the CLI, so a
problem can be found later. Never written: message text, login codes, phone
numbers or passwords. No caller passes them, and phone-like numbers are masked
as a second guard. Logging never raises: a log that cannot be written is skipped.
"""

import logging
import logging.handlers
import os
import platform
import re
import sys
import threading
from pathlib import Path

from . import __version__

MAX_BYTES = 1_000_000
BACKUPS = 3
_PHONE = re.compile(r"\+\d[\d ]{6,}\d")
_lock = threading.Lock()
_loggers: dict[str, logging.Logger] = {}


def log_dir(home) -> Path:
    return Path(home) / "app" / "logs"


def log_path(home) -> Path:
    return log_dir(home) / "tgkit.log"


class _Masked(logging.Formatter):
    def format(self, record):
        return _PHONE.sub("+***", super().format(record))


class _Handler(logging.handlers.RotatingFileHandler):
    """Opens the file per record and closes it again: the window and a sync it
    started both write here, and Windows cannot rotate a file another one holds."""

    def emit(self, record):
        try:
            super().emit(record)
        finally:
            if self.stream:
                self.stream.close()
                self.stream = None

    def handleError(self, record):  # noqa: N802  (a broken log never shows up in the window)
        pass


def logger(home=None) -> logging.Logger:
    if home is None:
        from . import config  # noqa: PLC0415

        home = config.home()
    key = str(log_path(home))
    with _lock:
        lg = _loggers.get(key)
        if lg is None:
            lg = logging.getLogger(f"tgkit.file{len(_loggers)}")
            lg.propagate = False
            lg.setLevel(logging.INFO)
            try:
                log_dir(home).mkdir(parents=True, exist_ok=True)
                h = _Handler(key, maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8", delay=True)
                h.setFormatter(_Masked("%(asctime)s %(levelname)s tgkit " + __version__
                                       + " pid=%(process)d %(threadName)s: %(message)s"))
                lg.addHandler(h)
            except OSError:
                pass
            _loggers[key] = lg
        return lg


def caught(where: str, exc: BaseException | None = None, home=None) -> None:
    """Write a caught error with its traceback. `where` says what was being done
    (never a chat name or a message)."""
    try:
        info = (type(exc), exc, exc.__traceback__) if exc is not None else True
        logger(home).error(where, exc_info=info)
    except Exception:  # noqa: BLE001
        pass


def note(text: str, home=None) -> None:
    try:
        logger(home).info(text)
    except Exception:  # noqa: BLE001
        pass


def tail(home, lines: int = 80) -> list[str]:
    try:
        text = log_path(home).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return text.splitlines()[-lines:]


def diagnostics(home, facts: dict) -> str:
    """Plain text to paste to whoever helps: versions, state, the last log lines.
    No messages, codes, phone numbers or passwords."""
    try:
        import telethon  # noqa: PLC0415

        tele = telethon.__version__
    except Exception:  # noqa: BLE001
        tele = "not installed"
    out = ["TG Kit diagnostics",
           f"TG Kit {__version__}, Python {platform.python_version()}, Telethon {tele}",
           f"System: {platform.platform()} ({sys.platform}, pid {os.getpid()})",
           f"Data folder: {home}",
           f"Log file: {log_path(home)}"]
    out += [f"{k}: {v}" for k, v in facts.items()]
    last = tail(home)
    out.append("")
    out.append("Last log lines:" if last else "Last log lines: none (no error was logged)")
    out += last
    return _PHONE.sub("+***", "\n".join(out)) + "\n"
