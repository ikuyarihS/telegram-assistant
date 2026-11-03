"""`tgkit app`: open Telegram Assistant as a desktop window.

The window is the system's own web view (Edge WebView2 on Windows, WebKit on
macOS) through pywebview, showing the local UI from appserver. Without
pywebview the same UI opens in the default browser; the server then stops by
itself a minute after the page is closed.
"""

import argparse
import inspect
import os
import signal
import sys
import threading
import time
import webbrowser
from importlib import resources

from . import applog, config
from .appserver import App, serve

IDLE_STOP = 90  # seconds without a ping from an open page (browser mode only)


TITLE = "Telegram Assistant"
APP_ID = "TelegramAssistant.App"  # Windows groups the taskbar button (and its icon) by this


def icon_path(name: str = "tgkit.ico") -> str | None:
    """The app icon shipped in the package (scripts/make-icons.py makes it)."""
    try:
        path = resources.files("tgkit").joinpath("web", "static", name)
        return str(path) if path.is_file() else None
    except (OSError, TypeError, ModuleNotFoundError):
        return None


def _windows_icon(title: str, ico: str | None) -> None:
    """Windows: give the window and its taskbar button the app icon, not Python's.
    Best effort: a failure only leaves the default icon."""
    if os.name != "nt" or not ico:
        return
    try:
        import ctypes  # noqa: PLC0415

        user32 = ctypes.windll.user32
        user32.FindWindowW.restype = ctypes.c_void_p
        user32.LoadImageW.restype = ctypes.c_void_p
        user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
        hwnd = None
        for _ in range(100):  # the window appears a moment after start()
            hwnd = user32.FindWindowW(None, title)
            if hwnd:
                break
            time.sleep(0.1)
        if not hwnd:
            return
        for which, px in ((1, 32), (0, 16)):  # ICON_BIG, ICON_SMALL
            hicon = user32.LoadImageW(None, ico, 1, px, px, 0x10)  # IMAGE_ICON, LR_LOADFROMFILE
            if hicon:
                user32.SendMessageW(hwnd, 0x80, which, hicon)  # WM_SETICON
    except Exception:  # noqa: BLE001
        pass


def _app_id() -> None:
    if os.name == "nt":
        try:
            import ctypes  # noqa: PLC0415

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        except Exception:  # noqa: BLE001
            pass


def _window(url: str, home=None, app=None) -> bool:
    try:
        import webview  # noqa: PLC0415
    except ImportError:
        return False
    try:
        _app_id()
        win = webview.create_window(TITLE, url, width=1200, height=800, min_size=(720, 520),
                                    text_select=True)
        if app is not None:  # Update: the window closes, the update finishes after it
            app.quit_hook = win.destroy
        kw = {}
        # Windows (WinForms) loads it with System.Drawing.Icon, which only takes an .ico:
        # a PNG there kills the whole app on the .NET thread, past any try/except here.
        icon = icon_path() if os.name == "nt" else icon_path("icon-256.png")
        if icon and "icon" in inspect.signature(webview.start).parameters:
            kw["icon"] = icon
        threading.Thread(target=_windows_icon, args=(TITLE, icon_path()), daemon=True).start()
        webview.start(**kw)
    except Exception as exc:  # noqa: BLE001
        applog.caught("the window could not open", exc, home)  # the details go to the log file
        print("The Telegram Assistant window could not open; using the browser.", file=sys.stderr)
        return False
    return True


def _stop_on_sigterm() -> None:
    """A polite stop (SIGTERM) ends the app the same way as closing it: through the
    `finally` below, so the window's own sync ends too."""
    try:
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    except (ValueError, OSError, AttributeError):  # not the main thread, or no SIGTERM here
        pass


def run(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="tgkit app", add_help=True)
    ap.add_argument("--browser", action="store_true", help="open in the web browser, not a window")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--no-window", action="store_true", help="only run the server and print its address")
    ap.add_argument("--token", default=None, help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    app = App(config.home(), token=args.token)
    httpd = serve(app, args.port)
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    worker = threading.Thread(target=httpd.serve_forever, daemon=True)
    worker.start()
    _stop_on_sigterm()
    try:
        if args.no_window:
            print(f"Telegram Assistant is running at {url}  (Ctrl+C stops it)", flush=True)
            while True:
                time.sleep(3600)
        if not args.browser and _window(url, app.home, app):
            return 0

        def _quit() -> None:  # Update in the browser: stop serving, like a closed page
            app.last_seen = -IDLE_STOP

        app.quit_hook = _quit
        webbrowser.open(url)
        print(f"Telegram Assistant opened in your browser: {url}", flush=True)
        app.last_seen = time.monotonic() + 60  # give the browser time to load the page
        while time.monotonic() - app.last_seen < IDLE_STOP:
            time.sleep(5)
        return 0
    except KeyboardInterrupt:
        return 0
    finally:
        # Closing the window ends the server; a `tgkit sync` the window started ends
        # with it (only its own processes, the same rules as Stop and retry).
        app.close()
        httpd.shutdown()


def main() -> None:
    """Entry point of the `tgkit-app` desktop shortcut (no console window)."""
    if sys.stdout is None or sys.stderr is None:  # pythonw: nowhere to print
        import io  # noqa: PLC0415
        sys.stdout = sys.stdout or io.StringIO()
        sys.stderr = sys.stderr or io.StringIO()
    raise SystemExit(run([]))
