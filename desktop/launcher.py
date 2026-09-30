from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

from werkzeug.serving import BaseWSGIServer, make_server


APP_NAME = "Alice Pro"
SERVER_HOST = "127.0.0.1"
DEFAULT_WINDOW_WIDTH = 1440
DEFAULT_WINDOW_HEIGHT = 900
MIN_WINDOW_SIZE = (960, 640)


def desktop_data_dir() -> Path:
    configured = os.environ.get("ALICE_DESKTOP_DATA_DIR")
    if configured:
        return Path(configured).expanduser().resolve()

    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / APP_NAME

    return Path.home() / "AppData" / "Local" / APP_NAME


def configure_environment() -> Path:
    data_dir = desktop_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)

    os.environ.setdefault("ALICE_DB_PATH", str(data_dir / "alice_pro.db"))
    os.environ.setdefault("HOST", SERVER_HOST)
    os.environ.setdefault("FLASK_DEBUG", "0")
    os.environ["ALICE_DESKTOP"] = "1"
    return data_dir


def create_server(flask_app: Any) -> BaseWSGIServer:
    return make_server(SERVER_HOST, 0, flask_app, threaded=True)


def server_url(server: BaseWSGIServer) -> str:
    return f"http://{SERVER_HOST}:{server.server_port}/"


def run_desktop() -> None:
    configure_environment()

    # Import only after the desktop environment is configured. Several backend
    # modules read database/runtime paths during import.
    from app import app

    server = create_server(app)
    server_thread = threading.Thread(
        target=server.serve_forever,
        name="alice-pro-desktop-server",
        daemon=True,
    )
    server_thread.start()

    try:
        import webview

        webview.create_window(
            APP_NAME,
            server_url(server),
            width=DEFAULT_WINDOW_WIDTH,
            height=DEFAULT_WINDOW_HEIGHT,
            min_size=MIN_WINDOW_SIZE,
        )
        webview.start(debug=False)
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)


if __name__ == "__main__":  # pragma: no cover - executable entrypoint
    run_desktop()
