"""Android bootstrap for the existing Flask application."""

from __future__ import annotations

import os
import socket
import threading
import traceback
from pathlib import Path
from typing import Optional


HOST = "127.0.0.1"
PORT = 5000

_LOCAL_AGENT_WORKER = None
_LOCAL_AGENT_THREAD = None
_LOCAL_AGENT_LOCK = threading.Lock()


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


def start_local_agent(
    gateway_url: str,
    bootstrap_token: str,
    *,
    agent_id: Optional[str] = None,
    capabilities: Optional[list[str]] = None,
) -> dict:
    """Register and start the outbound Local Tool Agent worker.

    The runtime token returned by Cloud.ru is held only in the Python process.
    The Android UI stores only the bootstrap credential needed for a future
    re-registration.
    """
    global _LOCAL_AGENT_WORKER, _LOCAL_AGENT_THREAD

    with _LOCAL_AGENT_LOCK:
        if _LOCAL_AGENT_THREAD is not None and _LOCAL_AGENT_THREAD.is_alive():
            return {
                "status": "already_running",
                "agent_id": getattr(_LOCAL_AGENT_WORKER, "agent_id", agent_id),
            }

        from local_tool_agent import start_agent

        worker, _runtime_token = start_agent(
            gateway_url,
            bootstrap_token,
            agent_id=agent_id,
            name="Alice Pro Android Agent",
            capabilities=capabilities or ["local.tools"],
            version="1.0",
        )
        thread = threading.Thread(
            target=worker.run_forever,
            name="alice-local-tool-agent",
            daemon=True,
        )
        _LOCAL_AGENT_WORKER = worker
        _LOCAL_AGENT_THREAD = thread
        thread.start()

        return {
            "status": "started",
            "agent_id": worker.agent_id,
        }


def local_agent_status() -> dict:
    with _LOCAL_AGENT_LOCK:
        return {
            "running": bool(
                _LOCAL_AGENT_THREAD is not None
                and _LOCAL_AGENT_THREAD.is_alive()
            ),
            "agent_id": getattr(_LOCAL_AGENT_WORKER, "agent_id", None),
        }
