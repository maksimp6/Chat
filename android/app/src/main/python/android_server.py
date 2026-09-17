"""Android bootstrap for the existing Flask application."""

from __future__ import annotations

import os
import socket
import traceback
from pathlib import Path
from typing import Optional


HOST = "127.0.0.1"
PORT = 5000


def _server_is_running() -> bool:
    """Return whether the local Flask port is already accepting connections."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.25)
        return probe.connect_ex((HOST, PORT)) == 0


def _write_startup_error(home: str, error: BaseException) -> None:
    """Persist the complete startup traceback for diagnostics on the device."""
    try:
        log_path = Path(home) / "alice_pro_startup_error.log"
        log_path.write_text(traceback.format_exc(), encoding="utf-8")
    except Exception:
        pass


def start_server(api_key: Optional[str] = None):
    """Prepare Android-private paths and start the existing Flask app.

    Android may recreate the Activity while the Python interpreter remains
    alive. In that case a second attempt to bind port 5000 must be harmless.
    """
    if api_key:
        os.environ["YANDEX_API_KEY"] = api_key

    home = os.environ.get("HOME") or os.getcwd()
    local_repo = os.path.join(home, "repo")
    os.makedirs(local_repo, exist_ok=True)
    os.environ["ALICE_LOCAL_REPO_DIR"] = local_repo
    os.chdir(home)

    if _server_is_running():
        return

    try:
        # Import only after the runtime environment is ready because config.py
        # validates Yandex credentials during import.
        from app import app

        app.run(host=HOST, port=PORT, threaded=True, use_reloader=False)
    except SystemExit as error:
        # Werkzeug can raise SystemExit(1) when another Activity already owns
        # the port. Treat that case as a successful, already-running server.
        if _server_is_running():
            return
        _write_startup_error(home, error)
        raise
    except BaseException as error:
        _write_startup_error(home, error)
        raise
