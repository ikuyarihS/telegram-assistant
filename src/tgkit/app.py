"""`tgkit app`: open TG Kit as a desktop window.

The window is the system's own web view (Edge WebView2 on Windows, WebKit on
macOS) through pywebview, showing the local UI from appserver. Without
pywebview the same UI opens in the default browser; the server then stops by
itself a minute after the page is closed.
"""

import argparse
import signal
import sys
import threading
import time
import webbrowser

from . import applog, config
from .appserver import App, serve

IDLE_STOP = 90  # seconds without a ping from an open page (browser mode only)


def _window(url: str, home=None) -> bool:
    try:
        import webview  # noqa: PLC0415
    except ImportError:
        return False
    try:
        webview.create_window("TG Kit", url, width=1200, height=800, min_size=(720, 520),
                              text_select=True)
        webview.start()
    except Exception as exc:  # noqa: BLE001
        applog.caught("the window could not open", exc, home)  # the details go to the log file
        print("The TG Kit window could not open; using the browser.", file=sys.stderr)
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
            print(f"TG Kit is running at {url}  (Ctrl+C stops it)", flush=True)
            while True:
                time.sleep(3600)
        if not args.browser and _window(url, app.home):
            return 0
        webbrowser.open(url)
        print(f"TG Kit opened in your browser: {url}", flush=True)
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
