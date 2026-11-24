"""Start the local study app: python run.py [--port 8776] [--data path]."""

from __future__ import annotations

import argparse
import os
import sys
import webbrowser
from pathlib import Path

from distiller.server import ROOT, serve
from distiller.storage import load_paths


def default_data_root() -> Path:
    if getattr(sys, "frozen", False):
        base = os.environ.get("LOCALAPPDATA")
        return (Path(base) if base else Path.home() / "AppData" / "Local") / "FrameworkDistiller"
    return ROOT / "data"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8776)
    parser.add_argument("--data", type=Path, help="Override the in-app storage choice for this run")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    default_root = default_data_root()
    settings_file = default_root / "settings.json"
    if args.data is None:
        data_root, inbox_root = load_paths(settings_file, default_root)
        server = serve(data_root, args.port, inbox_root=inbox_root,
                       settings_file=settings_file)
    else:
        # A command-line override is intentionally not persisted to the normal app settings.
        server = serve(args.data, args.port)
    url = f"http://127.0.0.1:{server.server_port}/"
    if sys.stdout is not None:
        print(f"Framework Distiller: {url}")
        print(f"Local data: {server.local_app.data_root}")
        print(f"Import inbox: {server.local_app.inbox}")
        print("Press Ctrl+C or use Quit in the app to stop. AI is off unless you add your own key and explicitly send an excerpt.")
    if not args.no_browser:
        # Fragment stays out of HTTP requests and local server logs. The page
        # exchanges it for an HttpOnly, process-lifetime session cookie.
        webbrowser.open(f"{url}#launch={server.local_app.session_token}")
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.local_app.ai.forget_all()
        server.server_close()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        if getattr(sys, "frozen", False) and os.name == "nt":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, str(exc), "Framework Distiller could not start", 0x10)
            raise SystemExit(1) from None
        raise
