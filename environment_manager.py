"""Branch-aware application environment control plane.

The manager owns immutable environment metadata and a small local runtime adapter.
It never mutates an environment's source after creation: a new commit gets a new
environment id. Production preview infrastructure may use the same metadata
contract through its deployment workflow.
"""

from __future__ import annotations

import json
import os
import subprocess
import shlex
import socket
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from db import get_conn
from invocation_context import InvocationContext
from invocation_trace import create_invocation_trace
from trace_manager import ExecutionTrace


ENV_STATUSES = ("CREATING", "RUNNING", "STOPPED", "FAILED", "DELETING")
_ALLOWED_TRANSITIONS = {
    "CREATING": {"STOPPED", "FAILED"},
    "STOPPED": {"RUNNING", "DELETING"},
    "RUNNING": {"STOPPED", "DELETING", "FAILED"},
    "FAILED": {"STOPPED", "DELETING"},
    "DELETING": set(),
}


def _now() -> int:
    return int(time.time())


def _repo_root() -> Path:
    return Path(os.environ.get("ALICE_ENV_REPO_ROOT") or os.getcwd()).resolve()


def _run_git(*args: str, cwd: Optional[Path] = None) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd or _repo_root()),
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    if result.returncode:
        raise ValueError(result.stderr.strip() or "git command failed")
    return result.stdout.strip()


def resolve_commit(branch: str, commit_sha: Optional[str] = None) -> str:
    """Resolve a branch/ref to one immutable commit without shell evaluation."""
    value = str(commit_sha or branch or "").strip()
    if not value:
        raise ValueError("branch is required")
    if any(ch in value for ch in ("\x00", "\n", "\r")):
        raise ValueError("invalid git ref")
    try:
        resolved = _run_git("rev-parse", "--verify", f"{value}^{{commit}}")
    except ValueError as exc:
        raise ValueError(f"git ref is not available: {value}") from exc
    if len(resolved) != 40 or any(ch not in "0123456789abcdef" for ch in resolved.lower()):
        raise ValueError("git did not return a commit SHA")
    return resolved


def init_environment_tables() -> None:
    conn = get_conn()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS environments (
                environment_id TEXT PRIMARY KEY,
                branch_name TEXT NOT NULL,
                commit_sha TEXT NOT NULL,
                status TEXT NOT NULL,
                url TEXT,
                runtime_pid INTEGER,
                runtime_port INTEGER,
                data_namespace TEXT NOT NULL,
                owner_id TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                started_at INTEGER,
                stopped_at INTEGER,
                deleted_at INTEGER,
                error TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS environment_events (
                event_id TEXT PRIMARY KEY,
                environment_id TEXT NOT NULL,
                operation TEXT NOT NULL,
                status TEXT NOT NULL,
                trace_id TEXT NOT NULL,
                trace_json TEXT NOT NULL,
                created_at INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_environments_owner ON environments(owner_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_environments_status ON environments(status)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_environment_events_env ON environment_events(environment_id)"
        )
        conn.commit()
    finally:
        conn.close()


def _transition(current: str, target: str) -> None:
    if target not in _ALLOWED_TRANSITIONS.get(current, set()):
        raise ValueError(f"invalid environment transition: {current} -> {target}")


def _row_to_dict(row) -> Dict[str, Any]:
    return dict(row)


def _get(environment_id: str) -> Optional[Dict[str, Any]]:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM environments WHERE environment_id = ?",
            (environment_id,),
        ).fetchone()
        return _row_to_dict(row) if row else None
    finally:
        conn.close()


def _owned(case: Dict[str, Any], owner_id: Optional[str]) -> bool:
    return not owner_id or not case.get("owner_id") or case.get("owner_id") == owner_id


def _require(environment_id: str, owner_id: Optional[str] = None) -> Dict[str, Any]:
    item = _get(environment_id)
    if not item or not _owned(item, owner_id):
        raise KeyError("environment_not_found")
    return item


def _public_url(environment_id: str) -> str:
    base = os.environ.get("ALICE_ENV_PUBLIC_BASE_URL", "").rstrip("/")
    if base:
        return f"{base}/environments/{environment_id}/"
    return f"/environments/{environment_id}/"


def _namespace(environment_id: str) -> str:
    return f"env_{environment_id.replace('-', '_')}"


def _record_trace(
    environment: Dict[str, Any],
    operation: str,
    status: str,
    *,
    context: Optional[InvocationContext] = None,
    error: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    trace = create_invocation_trace(context) if context else ExecutionTrace()
    if not context:
        trace.set_context(
            invocation_id=f"environment:{environment['environment_id']}",
            session_id=f"environment:{environment['environment_id']}",
            conversation_id=f"environment:{environment['environment_id']}",
        )
    trace.set_request({
        "operation": operation,
        "environment_id": environment["environment_id"],
        "branch": environment["branch_name"],
        "commit_sha": environment["commit_sha"],
    })
    trace.add_event("environment_lifecycle", {
        "environment_id": environment["environment_id"],
        "branch": environment["branch_name"],
        "commit_sha": environment["commit_sha"],
        "operation": operation,
        "status": status,
        **(extra or {}),
    })
    if error:
        trace.record_error(operation, error)
    trace.finalize()
    trace_json = trace.trace
    conn = get_conn()
    try:
        conn.execute(
            """
            INSERT INTO environment_events
            (event_id, environment_id, operation, status, trace_id, trace_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                environment["environment_id"],
                operation,
                status,
                trace.trace_id,
                json.dumps(trace_json, ensure_ascii=False),
                _now(),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return trace_json


class EnvironmentRuntime:
    """Process runtime for an immutable git worktree.

    Runtime commands are configured server-side. The default command is the
    repository's Flask app, which keeps the feature useful locally and in CI.
    """

    def __init__(self, environment: Dict[str, Any]):
        self.environment = environment
        self.root = Path(
            os.environ.get("ALICE_ENV_RUNTIME_ROOT") or ".alice-environments"
        ).resolve()
        self.worktree = self.root / environment["environment_id"] / "source"
        self.data_dir = self.root / environment["environment_id"] / "data"

    def _command(self) -> list[str]:
        raw = os.environ.get("ALICE_ENV_RUNTIME_COMMAND", "python app.py")
        return shlex.split(raw)

    def _port(self) -> int:
        requested = int(self.environment.get("runtime_port") or 0)
        if requested:
            return requested
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    def start(self) -> tuple[int, int]:
        self.root.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.worktree.parent.mkdir(parents=True, exist_ok=True)
        if not self.worktree.exists():
            subprocess.run(
                ["git", "worktree", "add", "--detach", str(self.worktree), self.environment["commit_sha"]],
                cwd=str(_repo_root()),
                check=True,
                capture_output=True,
                text=True,
                timeout=60,
            )
        port = self._port()
        log_path = self.data_dir / "runtime.log"
        log = open(log_path, "ab")
        env = os.environ.copy()
        env.update({
            "ALICE_ENV_ID": self.environment["environment_id"],
            "GIT_BRANCH": self.environment["branch_name"],
            "GIT_COMMIT_SHA": self.environment["commit_sha"],
            "ALICE_ENV_NAMESPACE": self.environment["data_namespace"],
            "ALICE_PREVIEW_BASE_PATH": "/environments/" + self.environment["environment_id"],
            "ALICE_DB_PATH": str(self.data_dir / "alice_pro.db"),
            "HOST": "127.0.0.1",
            "PORT": str(port),
        })
        process = subprocess.Popen(
            self._command(),
            cwd=str(self.worktree),
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        return process.pid, port

    def stop(self, pid: Optional[int]) -> None:
        if not pid:
            return
        try:
            os.kill(int(pid), 15)
        except ProcessLookupError:
            pass

    def remove(self) -> None:
        if self.worktree.exists():
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(self.worktree)],
                cwd=str(_repo_root()),
                check=False,
                capture_output=True,
                text=True,
                timeout=60,
            )
        import shutil
        shutil.rmtree(self.worktree.parent, ignore_errors=True)


def create_environment(
    branch: str,
    commit_sha: Optional[str] = None,
    owner_id: Optional[str] = None,
    *,
    context: Optional[InvocationContext] = None,
) -> Dict[str, Any]:
    init_environment_tables()
    resolved = resolve_commit(branch, commit_sha)
    environment_id = str(uuid.uuid4())
    item = {
        "environment_id": environment_id,
        "branch_name": branch.strip(),
        "commit_sha": resolved,
        "status": "CREATING",
        "url": _public_url(environment_id),
        "runtime_pid": None,
        "runtime_port": None,
        "data_namespace": _namespace(environment_id),
        "owner_id": owner_id,
        "created_at": _now(),
        "updated_at": _now(),
        "started_at": None,
        "stopped_at": None,
        "deleted_at": None,
        "error": None,
    }
    conn = get_conn()
    try:
        conn.execute(
            """
            INSERT INTO environments
            (environment_id, branch_name, commit_sha, status, url, runtime_pid,
             runtime_port, data_namespace, owner_id, created_at, updated_at,
             started_at, stopped_at, deleted_at, error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            tuple(item.values()),
        )
        conn.commit()
    finally:
        conn.close()
    _transition("CREATING", "STOPPED")
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE environments SET status = ?, updated_at = ? WHERE environment_id = ?",
            ("STOPPED", _now(), environment_id),
        )
        conn.commit()
    finally:
        conn.close()
    item["status"] = "STOPPED"
    _record_trace(item, "CREATE_ENVIRONMENT", "SUCCESS", context=context)
    return item


def list_environments(owner_id: Optional[str] = None) -> list[Dict[str, Any]]:
    init_environment_tables()
    conn = get_conn()
    try:
        if owner_id:
            rows = conn.execute(
                "SELECT * FROM environments WHERE owner_id = ? OR owner_id IS NULL ORDER BY created_at DESC",
                (owner_id,),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM environments ORDER BY created_at DESC").fetchall()
        return [_row_to_dict(row) for row in rows]
    finally:
        conn.close()


def start_environment(environment_id: str, owner_id: Optional[str] = None, *, context=None) -> Dict[str, Any]:
    item = _require(environment_id, owner_id)
    _transition(item["status"], "RUNNING")
    runtime = EnvironmentRuntime(item)
    try:
        pid, port = runtime.start()
        conn = get_conn()
        try:
            conn.execute(
                """
                UPDATE environments
                   SET status = ?, runtime_pid = ?, runtime_port = ?, started_at = ?,
                       updated_at = ?, error = NULL
                 WHERE environment_id = ?
                """,
                ("RUNNING", pid, port, _now(), _now(), environment_id),
            )
            conn.commit()
        finally:
            conn.close()
        item.update({"status": "RUNNING", "runtime_pid": pid, "runtime_port": port, "started_at": _now(), "error": None})
        _record_trace(item, "START_ENVIRONMENT", "SUCCESS", context=context, extra={"runtime_port": port})
        return item
    except Exception as exc:
        conn = get_conn()
        try:
            conn.execute(
                "UPDATE environments SET status = ?, error = ?, updated_at = ? WHERE environment_id = ?",
                ("FAILED", str(exc), _now(), environment_id),
            )
            conn.commit()
        finally:
            conn.close()
        item.update({"status": "FAILED", "error": str(exc)})
        _record_trace(item, "START_ENVIRONMENT", "FAILED", context=context, error=str(exc))
        raise


def stop_environment(environment_id: str, owner_id: Optional[str] = None, *, context=None) -> Dict[str, Any]:
    item = _require(environment_id, owner_id)
    _transition(item["status"], "STOPPED")
    EnvironmentRuntime(item).stop(item.get("runtime_pid"))
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE environments SET status = ?, runtime_pid = NULL, updated_at = ?, stopped_at = ? WHERE environment_id = ?",
            ("STOPPED", _now(), _now(), environment_id),
        )
        conn.commit()
    finally:
        conn.close()
    item.update({"status": "STOPPED", "runtime_pid": None, "stopped_at": _now()})
    _record_trace(item, "STOP_ENVIRONMENT", "SUCCESS", context=context)
    return item


def restart_environment(environment_id: str, owner_id: Optional[str] = None, *, context=None) -> Dict[str, Any]:
    item = _require(environment_id, owner_id)
    if item["status"] == "RUNNING":
        stop_environment(environment_id, owner_id, context=context)
    return start_environment(environment_id, owner_id, context=context)


def delete_environment(environment_id: str, owner_id: Optional[str] = None, *, context=None) -> Dict[str, Any]:
    item = _require(environment_id, owner_id)
    if item["status"] == "RUNNING":
        stop_environment(environment_id, owner_id, context=context)
        item = _require(environment_id, owner_id)
    _transition(item["status"], "DELETING")
    EnvironmentRuntime(item).remove()
    item.update({"status": "DELETING", "deleted_at": _now()})
    _record_trace(item, "DELETE_ENVIRONMENT", "SUCCESS", context=context)
    conn = get_conn()
    try:
        conn.execute("DELETE FROM environments WHERE environment_id = ?", (environment_id,))
        conn.commit()
    finally:
        conn.close()
    return item
