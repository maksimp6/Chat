"""Android bootstrap for the existing Flask application."""

from __future__ import annotations

import json
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
_LOCAL_AGENT_CONFIG = None


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
    """Prepare Android-private paths and start the existing Flask app."""
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
        from app import app

        app.run(host=HOST, port=PORT, threaded=True, use_reloader=False)
    except SystemExit as error:
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
    agent_id: Optional[str] = None,
    capabilities: Optional[list[str]] = None,
) -> str:
    """Register and start the outbound Local Tool Agent worker."""
    global _LOCAL_AGENT_WORKER, _LOCAL_AGENT_THREAD, _LOCAL_AGENT_CONFIG

    with _LOCAL_AGENT_LOCK:
        if _LOCAL_AGENT_THREAD is not None and _LOCAL_AGENT_THREAD.is_alive():
            return json.dumps(
                {
                    "status": "already_running",
                    "agent_id": getattr(_LOCAL_AGENT_WORKER, "agent_id", agent_id),
                },
                ensure_ascii=False,
            )

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
        _LOCAL_AGENT_CONFIG = {
            "gateway_url": gateway_url,
            "bootstrap_token": bootstrap_token,
            "agent_id": agent_id,
            "capabilities": list(capabilities or ["local.tools"]),
        }
        thread.start()

        return json.dumps(
            {
                "status": "started",
                "agent_id": worker.agent_id,
            },
            ensure_ascii=False,
        )


def stop_local_agent() -> dict:
    """Stop the in-process local agent without clearing its restart configuration."""
    global _LOCAL_AGENT_WORKER, _LOCAL_AGENT_THREAD

    with _LOCAL_AGENT_LOCK:
        worker = _LOCAL_AGENT_WORKER
        thread = _LOCAL_AGENT_THREAD
        if worker is None or thread is None or not thread.is_alive():
            return {"running": False, "status": "already_stopped"}
        worker.stop_event.set()

    thread.join(timeout=5)

    with _LOCAL_AGENT_LOCK:
        _LOCAL_AGENT_WORKER = None
        _LOCAL_AGENT_THREAD = None
        return {"running": False, "status": "stopped"}


def restart_local_agent() -> dict:
    """Restart the local agent from the last in-memory app configuration."""
    global _LOCAL_AGENT_CONFIG

    with _LOCAL_AGENT_LOCK:
        config = dict(_LOCAL_AGENT_CONFIG or {})
    if not config:
        return {"running": False, "status": "setup_required"}

    stop_local_agent()
    result = json.loads(start_local_agent(**config))
    result["running"] = result.get("status") in {"started", "already_running"}
    return result


def local_agent_status() -> dict:
    """Return local-agent process state without exposing credentials."""
    with _LOCAL_AGENT_LOCK:
        return {
            "running": bool(
                _LOCAL_AGENT_THREAD is not None
                and _LOCAL_AGENT_THREAD.is_alive()
            ),
            "configured": bool(_LOCAL_AGENT_CONFIG),
            "agent_id": getattr(_LOCAL_AGENT_WORKER, "agent_id", None),
        }
