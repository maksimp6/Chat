#!/usr/bin/env python3
"""Deterministic local launch smoke for the Alice Pro P0 baseline.

Offline mode proves the local Flask/UI/session/restart path without external
provider credentials. Full mode additionally configures Yandex through the
supported provider-credentials API, creates a provider conversation, sends one
real chat request, verifies the persisted ExecutionTrace, and rechecks state
after restart.

Secret values are read only from environment variables and are never printed.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from typing import Any

from cryptography.fernet import Fernet
import requests


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PORT = 8765
REQUEST_TIMEOUT = 20.0
STARTUP_TIMEOUT = 45.0


class SmokeFailure(RuntimeError):
    """Safe failure that must not contain credential values."""


@dataclass
class RunningServer:
    process: subprocess.Popen
    log_handle: Any
    log_path: Path

    def stop(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self.log_handle.close()


def _git_output(*args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", *args],
            cwd=ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise SmokeFailure("git state could not be determined") from exc


def _git_state(*, require_master: bool) -> dict[str, str]:
    head = _git_output("rev-parse", "HEAD")
    dirty = _git_output("status", "--porcelain", "--untracked-files=no")
    if dirty:
        raise SmokeFailure("tracked working tree is not clean")

    if require_master:
        try:
            master = _git_output("rev-parse", "refs/remotes/origin/master")
        except SmokeFailure:
            master = _git_output("rev-parse", "master")
        if head != master:
            raise SmokeFailure("HEAD is not the current master ref")

    return {"head_sha": head}


def _json_response(response: requests.Response) -> Any:
    try:
        return response.json()
    except ValueError as exc:
        raise SmokeFailure(
            f"HTTP {response.status_code} returned non-JSON content"
        ) from exc


def _request_json(
    session: requests.Session,
    method: str,
    base_url: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
    expected: tuple[int, ...] = (200,),
) -> tuple[int, Any]:
    try:
        response = session.request(
            method,
            f"{base_url}{path}",
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise SmokeFailure(f"{method} {path} could not reach Alice") from exc

    data = _json_response(response)
    if response.status_code not in expected:
        error_code = data.get("error") if isinstance(data, dict) else None
        raise SmokeFailure(
            f"{method} {path} returned HTTP {response.status_code}"
            f" error={error_code or 'unknown'}"
        )
    return response.status_code, data


def _health_ok(session: requests.Session, base_url: str) -> None:
    _, payload = _request_json(session, "GET", base_url, "/healthz")
    if not isinstance(payload, dict) or payload.get("status") != "ok":
        raise SmokeFailure("/healthz returned an unexpected payload")


def _root_ok(session: requests.Session, base_url: str) -> None:
    try:
        response = session.get(f"{base_url}/", timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        raise SmokeFailure("GET / could not reach Alice") from exc
    if response.status_code != 200:
        raise SmokeFailure(f"GET / returned HTTP {response.status_code}")
    if "text/html" not in response.headers.get("content-type", "").lower():
        raise SmokeFailure("GET / did not return HTML")
    if not response.content:
        raise SmokeFailure("GET / returned an empty body")


def _wait_ready(
    session: requests.Session,
    base_url: str,
    process: subprocess.Popen,
) -> None:
    deadline = time.monotonic() + STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise SmokeFailure(
                f"Alice exited during startup with code {process.returncode}"
            )
        try:
            _health_ok(session, base_url)
            return
        except SmokeFailure:
            time.sleep(0.5)
    raise SmokeFailure("Alice did not become healthy before the startup timeout")


def _start_server(
    *,
    env: dict[str, str],
    work_dir: Path,
    base_url: str,
    attempt: int,
) -> RunningServer:
    log_path = work_dir / f"alice-launch-smoke-{attempt}.log"
    log_handle = log_path.open("w", encoding="utf-8")
    try:
        process = subprocess.Popen(
            [sys.executable, "app.py"],
            cwd=ROOT,
            env=env,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            text=True,
        )
    except Exception:
        log_handle.close()
        raise

    server = RunningServer(process=process, log_handle=log_handle, log_path=log_path)
    try:
        _wait_ready(requests.Session(), base_url, process)
    except Exception:
        server.stop()
        raise
    return server


def _create_session(session: requests.Session, base_url: str) -> str:
    _, payload = _request_json(
        session,
        "POST",
        base_url,
        "/api/sessions",
        payload={"metadata": {"source": "local-launch-smoke"}},
        expected=(200, 201),
    )
    session_id = str(payload.get("id") or "").strip() if isinstance(payload, dict) else ""
    if not session_id:
        raise SmokeFailure("session bootstrap returned no id")
    return session_id


def _session_persisted(
    session: requests.Session,
    base_url: str,
    session_id: str,
) -> None:
    _, payload = _request_json(
        session,
        "GET",
        base_url,
        f"/api/sessions/{session_id}",
    )
    if not isinstance(payload, dict) or payload.get("id") != session_id:
        raise SmokeFailure("persisted session could not be reloaded")


def _provider_inputs() -> tuple[str, str]:
    api_key = os.getenv("ALICE_LAUNCH_SMOKE_YANDEX_API_KEY", "").strip()
    project_id = os.getenv("ALICE_LAUNCH_SMOKE_YANDEX_PROJECT_ID", "").strip()
    return api_key, project_id


def _configure_yandex(
    session: requests.Session,
    base_url: str,
    api_key: str,
    project_id: str,
) -> None:
    _, payload = _request_json(
        session,
        "PUT",
        base_url,
        "/api/provider-credentials",
        payload={
            "yandex_api_key": api_key,
            "yandex_project_id": project_id,
        },
    )
    _assert_yandex_connected(payload)


def _assert_yandex_connected(payload: Any) -> None:
    providers = payload.get("providers") if isinstance(payload, dict) else None
    if not isinstance(providers, list):
        raise SmokeFailure("provider status response has no provider list")
    for provider in providers:
        if not isinstance(provider, dict) or provider.get("provider") != "yandex":
            continue
        if provider.get("status") == "connected" and provider.get("authorization_ok") is True:
            return
        raise SmokeFailure("Yandex provider is not connected")
    raise SmokeFailure("Yandex provider status is missing")


def _create_conversation(session: requests.Session, base_url: str) -> str:
    _, payload = _request_json(
        session,
        "POST",
        base_url,
        "/api/conversations",
        payload={"title": "Alice launch smoke", "model": "aliceai-llm"},
        expected=(200, 201),
    )
    conversation_id = (
        str(payload.get("id") or "").strip() if isinstance(payload, dict) else ""
    )
    if not conversation_id:
        raise SmokeFailure("conversation creation returned no id")
    return conversation_id


def _send_live_chat(
    session: requests.Session,
    base_url: str,
    *,
    conversation_id: str,
    session_id: str,
) -> dict[str, Any]:
    _, payload = _request_json(
        session,
        "POST",
        base_url,
        "/api/chat",
        payload={
            "conversation_id": conversation_id,
            "session_id": session_id,
            "message": "Reply with one short sentence confirming this launch smoke request.",
            "model": "aliceai-llm",
        },
    )
    if not isinstance(payload, dict):
        raise SmokeFailure("chat returned a non-object payload")
    if not str(payload.get("reply") or "").strip():
        raise SmokeFailure("chat returned no reply")
    if not str(payload.get("invocation_id") or "").strip():
        raise SmokeFailure("chat returned no invocation id")
    if not str(payload.get("trace_id") or "").strip():
        raise SmokeFailure("chat returned no trace id")
    return payload


def _trace_persisted(
    session: requests.Session,
    base_url: str,
    *,
    invocation_id: str,
    trace_id: str,
) -> None:
    _, payload = _request_json(
        session,
        "GET",
        base_url,
        f"/api/invocations/{invocation_id}/trace",
    )
    if not isinstance(payload, dict):
        raise SmokeFailure("persisted trace returned a non-object payload")
    context = payload.get("context") or {}
    if context.get("invocation_id") != invocation_id:
        raise SmokeFailure("persisted trace invocation correlation is missing")
    if payload.get("trace_id") != trace_id and context.get("trace_id") != trace_id:
        raise SmokeFailure("persisted trace id does not match the chat response")


def _conversation_persisted(
    session: requests.Session,
    base_url: str,
    conversation_id: str,
) -> None:
    _, payload = _request_json(session, "GET", base_url, "/api/conversations")
    conversations = payload.get("conversations") if isinstance(payload, dict) else None
    if not isinstance(conversations, list):
        raise SmokeFailure("conversation list response is invalid")
    if not any(
        isinstance(item, dict) and str(item.get("id") or "") == conversation_id
        for item in conversations
    ):
        raise SmokeFailure("conversation did not survive restart")


def _build_runtime_env(*, db_path: Path, port: int) -> dict[str, str]:
    env = dict(os.environ)
    env["HOST"] = "127.0.0.1"
    env["PORT"] = str(port)
    env["FLASK_DEBUG"] = "0"
    env["ALICE_DB_PATH"] = str(db_path)
    env["ALICE_REQUIRE_SHORT_TOKEN"] = "0"
    env["ALICE_PROVIDER_CREDENTIALS_TOKEN"] = ""
    env.pop("ALICE_DATABASE_URL", None)
    if not env.get("ALICE_PROVIDER_CREDENTIAL_KEY", "").strip():
        env["ALICE_PROVIDER_CREDENTIAL_KEY"] = Fernet.generate_key().decode("ascii")
    return env


def run_smoke(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    git = _git_state(require_master=args.require_master)
    api_key, project_id = _provider_inputs()
    provider_ready = bool(api_key and project_id)
    provider_partial = bool(api_key) != bool(project_id)
    if provider_partial:
        raise SmokeFailure(
            "launch-smoke provider inputs must include both API key and project id"
        )

    temp = tempfile.TemporaryDirectory(prefix="alice-launch-smoke-")
    work_dir = Path(temp.name)
    db_path = Path(args.db_path).expanduser().resolve() if args.db_path else work_dir / "alice.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    base_url = f"http://127.0.0.1:{args.port}"
    runtime_env = _build_runtime_env(db_path=db_path, port=args.port)
    checks: dict[str, Any] = {
        "clean_git": True,
        "healthz": False,
        "root_ui": False,
        "session_bootstrap": False,
        "provider_configured": False,
        "real_chat": False,
        "trace_persisted": False,
        "restart_persistence": False,
    }
    server: RunningServer | None = None
    session_id = ""
    conversation_id = ""
    invocation_id = ""
    trace_id = ""

    try:
        server = _start_server(
            env=runtime_env,
            work_dir=work_dir,
            base_url=base_url,
            attempt=1,
        )
        http = requests.Session()
        _health_ok(http, base_url)
        checks["healthz"] = True
        _root_ok(http, base_url)
        checks["root_ui"] = True

        session_id = _create_session(http, base_url)
        checks["session_bootstrap"] = True

        if not args.offline and provider_ready:
            _configure_yandex(http, base_url, api_key, project_id)
            checks["provider_configured"] = True
            conversation_id = _create_conversation(http, base_url)
            chat = _send_live_chat(
                http,
                base_url,
                conversation_id=conversation_id,
                session_id=session_id,
            )
            checks["real_chat"] = True
            invocation_id = str(chat["invocation_id"])
            trace_id = str(chat["trace_id"])
            _trace_persisted(
                http,
                base_url,
                invocation_id=invocation_id,
                trace_id=trace_id,
            )
            checks["trace_persisted"] = True

        server.stop()
        server = None
        if not db_path.exists() or db_path.stat().st_size <= 0:
            raise SmokeFailure("SQLite database was not persisted")

        server = _start_server(
            env=runtime_env,
            work_dir=work_dir,
            base_url=base_url,
            attempt=2,
        )
        http = requests.Session()
        _health_ok(http, base_url)
        _root_ok(http, base_url)
        _session_persisted(http, base_url, session_id)

        if conversation_id:
            _conversation_persisted(http, base_url, conversation_id)
            _trace_persisted(
                http,
                base_url,
                invocation_id=invocation_id,
                trace_id=trace_id,
            )
            _, provider_status = _request_json(
                http,
                "GET",
                base_url,
                "/api/provider-credentials/status",
            )
            _assert_yandex_connected(provider_status)

        checks["restart_persistence"] = True

        if args.offline:
            status = "PARTIAL"
            exit_code = 0
            blocked = ["provider_configuration", "real_chat", "successful_chat_trace"]
        elif not provider_ready:
            status = "BLOCKED"
            exit_code = 2
            blocked = ["missing_launch_smoke_provider_credentials"]
        else:
            status = "PASS"
            exit_code = 0
            blocked = []

        result = {
            "status": status,
            "mode": "offline" if args.offline else "full",
            **git,
            "database_backend": "sqlite",
            "provider_inputs_present": provider_ready,
            "checks": checks,
            "blocked": blocked,
            "session_id": session_id,
            "conversation_id": conversation_id or None,
            "invocation_id": invocation_id or None,
            "trace_id": trace_id or None,
        }
        return exit_code, result
    finally:
        if server is not None:
            server.stop()
        if args.db_path:
            temp.cleanup()
        elif args.keep_db:
            # TemporaryDirectory would remove the evidence. Detach cleanup only
            # when the caller explicitly asks to inspect the generated DB/logs.
            temp._finalizer.detach()  # noqa: SLF001
        else:
            temp.cleanup()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Run deterministic local startup/session/restart checks without a real provider call.",
    )
    parser.add_argument(
        "--require-master",
        action="store_true",
        help="Fail unless HEAD exactly matches the fetched origin/master ref.",
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--db-path",
        help="Optional persistent SQLite path. Default is an isolated temporary database.",
    )
    parser.add_argument(
        "--keep-db",
        action="store_true",
        help="Keep the temporary database and process logs for local inspection.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if not 1024 <= args.port <= 65535:
        print(
            json.dumps(
                {"status": "FAIL", "reason": "port must be between 1024 and 65535"},
                ensure_ascii=False,
            )
        )
        return 1
    try:
        exit_code, result = run_smoke(args)
    except SmokeFailure as exc:
        print(
            json.dumps(
                {"status": "FAIL", "reason": str(exc)},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 1
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "FAIL",
                    "reason": f"unexpected {type(exc).__name__}",
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 1

    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
