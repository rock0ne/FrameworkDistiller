"""Android-only loopback startup; no remote listener or host integration."""

from __future__ import annotations

import os
from pathlib import Path
from threading import Thread

_server = None
_thread = None


def start(data_root: str, static_root: str) -> int:
    global _server, _thread
    if _server is not None:
        return _server.server_port
    os.environ["FRAMEWORK_DISTILLER_STATIC"] = static_root
    from distiller.server import serve

    _server = serve(Path(data_root), 0, platform="android")
    _thread = Thread(target=_server.serve_forever, kwargs={"poll_interval": 0.25},
                     daemon=True, name="framework-distiller-loopback")
    _thread.start()
    return _server.server_port


def session_token() -> str:
    if _server is None:
        raise RuntimeError("Local app has not started")
    return _server.local_app.session_token


def clear_keys() -> None:
    if _server is not None:
        _server.local_app.ai.forget_all()
