"""Branch-aware application environment control plane.

The manager owns immutable environment metadata and a small local runtime adapter.
It never mutates an environment's source after creation: a new commit gets a new
environment id. Production preview infrastructure may use the same metadata
contract through its deployment workflow.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from db import get_conn, init_db as init_runtime_db
from invocation.context import InvocationContext
from invocation.trace import create_invocation_trace
from trace_manager import ExecutionTrace
from runtime import RuntimeDispatcher, RuntimeLoader, RuntimeNotFound, RuntimeOwnerViolation
from runtime.request_context import bind_runtime_request


ENV_STATUSES = ("CREATING", "RUNNING", "STOPPED", "FAILED", "DELETING")

_RUNTIME_DISPATCHER = RuntimeDispatcher()
_RUNTIME_WORKERS: dict[str, "EnvironmentRuntime"] = {}
_RUNTIME_WORKERS_LOCK = threading.RLock()
_RUNTIME_STOP = object()
_ALLOWED_TRANSITIONS = {
    "CREATING": {"STOPPED", "FAILED"},
    "STOPPED": {"RUNNING", "DELETING"},
    "RUNNING": {"STOPPED", "DELETING", "FAILED"},
    "FAILED": {"STOPPED", "DELETING"},
    "DELETING": set(),
}


def _invoke_revision_runtime(context, payload):
    """Dispatcher boundary for code loaded from an immutable revision snapshot."""
    with _RUNTIME_WORKERS_LOCK:
        runtime = _RUNTIME_WORKERS.get(context.runtime_id)
    if runtime is None or not runtime.loader.loaded:
        raise RuntimeError("revision runtime is not loaded")
    return runtime.loader.invoke(str(payload.get("operation") or ""), payload.get("payload"))


_RUNTIME_DISPATCHER.register_operation("revision.invoke", _invoke_revision_runtime)


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
    branch_value = str(branch or "").strip()
    value = str(commit_sha or branch_value).strip()
    if not branch_value:
        raise ValueError("branch is required")
    if not value:
        raise ValueError("commit is required")
    if any(ch in value for ch in ("\x00", "\n", "\r")) or any(
        ch in branch_value for ch in ("\x00", "\n", "\r")
    ):
        raise ValueError("invalid git ref")
    try:
        resolved = _run_git("rev-parse", "--verify", f"{value}^{{commit}}")
        if commit_sha:
            branch_resolved = _run_git("rev-parse", "--verify", f"{branch_value}^{{commit}}")
            check = subprocess.run(
                ["git", "merge-base", "--is-ancestor", resolved, branch_resolved],
                cwd=str(_repo_root()),
                check=False,
                capture_output=True,
                text=True,
                timeout=15,
            )
            if check.returncode != 0:
                raise ValueError("commit is not reachable from branch")
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
                adapter TEXT NOT NULL DEFAULT 'local',
                ttl_seconds INTEGER,
                expires_at INTEGER,
                cloud_resource_id TEXT,
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
        existing_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(environments)").fetchall()
        }
        for column, statement in (
            ("adapter", "ALTER TABLE environments ADD COLUMN adapter TEXT NOT NULL DEFAULT 'local'"),
            ("ttl_seconds", "ALTER TABLE environments ADD COLUMN ttl_seconds INTEGER"),
            ("expires_at", "ALTER TABLE environments ADD COLUMN expires_at INTEGER"),
            ("cloud_resource_id", "ALTER TABLE environments ADD COLUMN cloud_resource_id TEXT"),
        ):
            if column not in existing_columns:
                conn.execute(statement)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_environments_owner ON environments(owner_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_environments_status ON environments(status)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_environment_events_env ON environment_events(environment_id)"
        )
        # Only local environments die with this process; a RUNNING Cloud.ru
        # sandbox survives a backend restart, so it must not be reconciled here.
        running_rows = conn.execute(
            "SELECT environment_id FROM environments WHERE status = ? AND adapter = ?",
            ("RUNNING", "local"),
        ).fetchall()
        with _RUNTIME_WORKERS_LOCK:
            active_runtime_ids = set(_RUNTIME_WORKERS)
        for row in running_rows:
            runtime_id = row["environment_id"] if hasattr(row, "keys") else row[0]
            if runtime_id not in active_runtime_ids:
                conn.execute(
                    """
                    UPDATE environments
                       SET status = ?, runtime_pid = NULL, runtime_port = NULL,
                           updated_at = ?, stopped_at = COALESCE(stopped_at, ?)
                     WHERE environment_id = ?
                    """,
                    ("STOPPED", _now(), _now(), runtime_id),
                )
        conn.commit()
    finally:
        conn.close()


def _transition(current: str, target: str) -> None:
    if target not in _ALLOWED_TRANSITIONS.get(current, set()):
        raise ValueError(f"invalid environment transition: {current} -> {target}")


def _runtime_thread_id(environment_id: str) -> Optional[int]:
    with _RUNTIME_WORKERS_LOCK:
        worker = _RUNTIME_WORKERS.get(environment_id)
    return worker.thread_id if worker else None


def _row_to_dict(row) -> Dict[str, Any]:
    item = dict(row)
    item["runtime_thread_id"] = _runtime_thread_id(item["environment_id"])
    return item


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
    trace.set_request(
        {
            "operation": operation,
            "environment_id": environment["environment_id"],
            "branch": environment["branch_name"],
            "commit_sha": environment["commit_sha"],
        }
    )
    trace.add_event(
        "environment_lifecycle",
        {
            "environment_id": environment["environment_id"],
            "branch": environment["branch_name"],
            "commit_sha": environment["commit_sha"],
            "operation": operation,
            "status": status,
            **(extra or {}),
        },
    )
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


class RuntimeHTTPStream:
    """Queue-backed response body produced by a runtime worker thread."""

    def __init__(
        self,
        status_code: int,
        headers: list[tuple[str, str]],
        events: queue.Queue,
        cancel: threading.Event,
    ):
        self.status_code = int(status_code)
        self.headers = headers
        self._events = events
        self._cancel = cancel
        self._closed = False

    def __iter__(self):
        try:
            while True:
                event = self._events.get()
                kind = event[0]
                if kind == "chunk":
                    yield event[1]
                elif kind == "error":
                    raise event[1]
                elif kind == "end":
                    return
        finally:
            self.close()

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._cancel.set()


def _stream_put(events: queue.Queue, event: tuple, cancel: threading.Event) -> bool:
    while not cancel.is_set():
        try:
            events.put(event, timeout=0.1)
            return True
        except queue.Full:
            continue
    return False


class EnvironmentRuntime:
    """Managed runtime thread for one immutable environment scope.

    The source worktree remains immutable metadata for the selected commit, but
    the runtime itself stays inside the Alice Pro Python process. Runtime work
    is executed only after RuntimeDispatcher binds the environment scope.
    """

    def __init__(self, environment: Dict[str, Any]):
        self.environment = environment
        self.runtime_id = environment["environment_id"]
        self.root = Path(
            os.environ.get("ALICE_ENV_RUNTIME_ROOT") or ".alice-environments"
        ).resolve()
        self.worktree = self.root / self.runtime_id / "source"
        self.data_dir = self.root / self.runtime_id / "data"
        self.loader = RuntimeLoader(
            runtime_id=self.runtime_id,
            revision=environment["commit_sha"],
            source_root=self.worktree,
            dispatcher=_RUNTIME_DISPATCHER,
        )
        self._jobs: queue.Queue = queue.Queue()
        self._thread = threading.Thread(
            target=self._run,
            name=f"alice-runtime-{self.runtime_id[:8]}",
            daemon=True,
        )
        self._stream_lock = threading.RLock()
        self._active_streams: dict[threading.Event, queue.Queue] = {}

    @property
    def thread_id(self) -> Optional[int]:
        return self._thread.ident

    def _prepare(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.worktree.parent.mkdir(parents=True, exist_ok=True)
        if not self.worktree.exists():
            subprocess.run(
                [
                    "git",
                    "worktree",
                    "add",
                    "--detach",
                    str(self.worktree),
                    self.environment["commit_sha"],
                ],
                cwd=str(_repo_root()),
                check=True,
                capture_output=True,
                text=True,
                timeout=60,
            )
        actual_commit = _run_git("rev-parse", "HEAD", cwd=self.worktree)
        if actual_commit != self.environment["commit_sha"]:
            raise RuntimeError("runtime worktree does not match the resolved commit")
        if _run_git("status", "--porcelain", cwd=self.worktree):
            raise RuntimeError("runtime worktree is not an immutable clean snapshot")
        self.loader.load()

    def start(self) -> int:
        self._prepare()
        with bind_runtime_request(
            self.runtime_id,
            "",
            data_root=str(self.data_dir),
        ):
            init_runtime_db()
        with _RUNTIME_WORKERS_LOCK:
            if self.runtime_id in _RUNTIME_WORKERS:
                raise RuntimeError("environment runtime is already running")
            _RUNTIME_WORKERS[self.runtime_id] = self
        try:
            _RUNTIME_DISPATCHER.register_runtime(
                self.runtime_id,
                owner_id=self.environment.get("owner_id"),
                namespace=self.environment["data_namespace"],
                root=str(self.data_dir),
            )
            self._thread.start()
            for _ in range(100):
                if self._thread.ident is not None:
                    return int(self._thread.ident)
                time.sleep(0.001)
            raise RuntimeError("environment runtime thread did not start")
        except Exception:
            with _RUNTIME_WORKERS_LOCK:
                _RUNTIME_WORKERS.pop(self.runtime_id, None)
            _RUNTIME_DISPATCHER.unregister_runtime(self.runtime_id)
            raise

    def submit(
        self,
        operation: str,
        payload: Optional[Dict[str, Any]] = None,
        *,
        timeout: float = 30.0,
    ) -> Any:
        if not self._thread.is_alive():
            raise RuntimeError("environment runtime is not running")
        result: queue.Queue = queue.Queue(maxsize=1)
        self._jobs.put(("call", operation, dict(payload or {}), result))
        try:
            kind, value = result.get(timeout=timeout)
        except queue.Empty as exc:
            raise TimeoutError("environment runtime operation timed out") from exc
        if kind == "error":
            raise value
        return value

    def submit_http(
        self,
        payload: Dict[str, Any],
        *,
        timeout: float = 30.0,
    ) -> RuntimeHTTPStream:
        if not self._thread.is_alive():
            raise RuntimeError("environment runtime is not running")
        events: queue.Queue = queue.Queue(maxsize=8)
        cancel = threading.Event()
        with self._stream_lock:
            self._active_streams[cancel] = events
        self._jobs.put(("http", "http.request", dict(payload), events, cancel))
        try:
            first = events.get(timeout=timeout)
        except queue.Empty as exc:
            cancel.set()
            raise TimeoutError("environment runtime HTTP request timed out") from exc
        if first[0] == "error":
            cancel.set()
            raise first[1]
        if first[0] != "start":
            cancel.set()
            raise RuntimeError("invalid environment runtime HTTP response")
        return RuntimeHTTPStream(first[1], first[2], events, cancel)

    def stop(self) -> None:
        with self._stream_lock:
            active_streams = tuple(self._active_streams.items())
        for cancel, events in active_streams:
            cancel.set()
            try:
                while True:
                    events.get_nowait()
            except queue.Empty:
                pass
            try:
                events.put_nowait(("end",))
            except queue.Full:
                pass
        if self._thread.is_alive():
            self._jobs.put(_RUNTIME_STOP)
            self._thread.join(timeout=5)
        if self._thread.is_alive():
            raise RuntimeError("environment runtime thread did not stop")
        with _RUNTIME_WORKERS_LOCK:
            if _RUNTIME_WORKERS.get(self.runtime_id) is self:
                _RUNTIME_WORKERS.pop(self.runtime_id, None)
        _RUNTIME_DISPATCHER.unregister_runtime(self.runtime_id)

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

    def _run(self) -> None:
        while True:
            job = self._jobs.get()
            if job is _RUNTIME_STOP:
                return
            kind = job[0]
            if kind == "call":
                _, operation, payload, result = job
                try:
                    with bind_runtime_request(
                        self.runtime_id,
                        "",
                        data_root=str(self.data_dir),
                    ):
                        value = _RUNTIME_DISPATCHER.dispatch(self.runtime_id, operation, payload)
                    result.put(("result", value))
                except Exception as exc:
                    result.put(("error", exc))
                continue

            _, operation, payload, events, cancel = job
            source = None
            try:
                base_path = str(payload.get("base_path") or "")
                with bind_runtime_request(
                    self.runtime_id,
                    base_path,
                    data_root=str(self.data_dir),
                ):
                    source = _RUNTIME_DISPATCHER.dispatch(self.runtime_id, operation, payload)
                    status_code = int(source["status_code"])
                    headers = list(source.get("headers") or [])
                    if not _stream_put(events, ("start", status_code, headers), cancel):
                        continue
                    for chunk in source.get("body") or ():
                        if cancel.is_set():
                            break
                        if isinstance(chunk, str):
                            chunk = chunk.encode("utf-8")
                        if chunk and not _stream_put(events, ("chunk", bytes(chunk)), cancel):
                            break
            except Exception as exc:
                _stream_put(events, ("error", exc), cancel)
            finally:
                if source is not None:
                    close = source.get("close")
                    if callable(close):
                        close()
                if not cancel.is_set():
                    _stream_put(events, ("end",), cancel)
                with self._stream_lock:
                    self._active_streams.pop(cancel, None)


class LocalEnvironmentAdapter:
    """Default `EnvironmentAdapter`: today's git-worktree + in-process thread runtime."""

    name = "local"

    def __init__(self, environment: Dict[str, Any]):
        self.environment = environment

    def provision(self) -> Dict[str, Any]:
        return {}

    def start(self) -> Dict[str, Any]:
        runtime = EnvironmentRuntime(self.environment)
        thread_id = runtime.start()
        return {"runtime_thread_id": thread_id}

    def execute(self, command: str, *, timeout_seconds: float = 30.0) -> Dict[str, Any]:
        with _RUNTIME_WORKERS_LOCK:
            runtime = _RUNTIME_WORKERS.get(self.environment["environment_id"])
        if runtime is None:
            raise RuntimeError("environment runtime is not running")
        completed = subprocess.run(
            ["/bin/bash", "-lc", command],
            shell=False,
            check=False,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            cwd=str(runtime.worktree),
            timeout=timeout_seconds,
        )
        return {
            "success": completed.returncode == 0,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "exit_code": completed.returncode,
        }

    def stop(self) -> None:
        with _RUNTIME_WORKERS_LOCK:
            runtime = _RUNTIME_WORKERS.get(self.environment["environment_id"])
        if runtime is not None:
            runtime.stop()

    def remove(self) -> None:
        EnvironmentRuntime(self.environment).remove()


_ENV_ADAPTER_NAMES = ("local", "cloudru")


def _validate_adapter_name(name: Optional[str]) -> str:
    value = str(name or "local").strip().lower()
    if value not in _ENV_ADAPTER_NAMES:
        raise ValueError(f"Unknown environment adapter: {value}")
    return value


def _adapter_for(environment: Dict[str, Any]):
    """Select the `EnvironmentAdapter` implementation for one environment record."""
    name = _validate_adapter_name(environment.get("adapter"))
    if name == "local":
        return LocalEnvironmentAdapter(environment)
    from cloud.cloudru.environment_adapter import CloudRuEnvironmentAdapter

    return CloudRuEnvironmentAdapter(environment)


def _default_ttl_seconds(adapter_name: str) -> Optional[int]:
    """Local environments keep today's behavior (no TTL) unless configured.

    Cloud.ru sandboxes default to a 1 hour TTL so an unattended Alice run
    cannot leave a paid resource allocated forever.
    """
    env_name = (
        "ALICE_ENV_DEFAULT_TTL_SECONDS"
        if adapter_name == "local"
        else "ALICE_CLOUDRU_ENV_DEFAULT_TTL_SECONDS"
    )
    fallback = "" if adapter_name == "local" else "3600"
    raw = os.environ.get(env_name, fallback).strip()
    if not raw:
        return None
    value = int(raw)
    return value if value > 0 else None


def _resolve_ttl_seconds(adapter_name: str, ttl_seconds: Optional[int]) -> Optional[int]:
    if ttl_seconds is not None:
        value = int(ttl_seconds)
        if value <= 0:
            raise ValueError("ttl_seconds must be > 0")
        return value
    return _default_ttl_seconds(adapter_name)


def register_runtime_operation(name: str, handler) -> None:
    """Register a dispatcher operation available to managed runtime threads."""
    _RUNTIME_DISPATCHER.register_operation(name, handler)


def authorize_environment_runtime(
    environment_id: str,
    owner_id: Optional[str],
) -> None:
    """Authorize gateway access without exposing another owner's environment."""
    try:
        _RUNTIME_DISPATCHER.authorize(environment_id, owner_id)
        return
    except RuntimeNotFound:
        # Stopped environments intentionally have no dispatcher context.  Keep
        # owner checks centralized here so the gateway can distinguish a
        # stopped owned environment (503) from a missing/cross-owner one (404).
        item = _get(environment_id)
        if not item:
            raise
        if owner_id and item.get("owner_id") not in (None, owner_id):
            raise RuntimeOwnerViolation(environment_id) from None


def dispatch_environment_operation(
    environment_id: str,
    operation: str,
    payload: Optional[Dict[str, Any]] = None,
    *,
    timeout: float = 30.0,
) -> Any:
    with _RUNTIME_WORKERS_LOCK:
        runtime = _RUNTIME_WORKERS.get(environment_id)
    if runtime is None:
        raise RuntimeError("environment runtime is not running")
    _RUNTIME_DISPATCHER.context(environment_id)
    return runtime.submit(operation, payload, timeout=timeout)


def dispatch_environment_revision(
    environment_id: str,
    operation: str,
    payload: Optional[Dict[str, Any]] = None,
    *,
    timeout: float = 30.0,
) -> Any:
    """Invoke the constrained revision application through its dispatcher scope."""
    return dispatch_environment_operation(
        environment_id,
        "revision.invoke",
        {"operation": operation, "payload": dict(payload or {})},
        timeout=timeout,
    )


def dispatch_environment_http(
    environment_id: str,
    payload: Dict[str, Any],
    *,
    timeout: float = 30.0,
) -> RuntimeHTTPStream:
    with _RUNTIME_WORKERS_LOCK:
        runtime = _RUNTIME_WORKERS.get(environment_id)
    if runtime is None:
        raise RuntimeError("environment runtime is not running")
    _RUNTIME_DISPATCHER.context(environment_id)
    return runtime.submit_http(payload, timeout=timeout)


def create_environment(
    branch: str,
    commit_sha: Optional[str] = None,
    owner_id: Optional[str] = None,
    *,
    context: Optional[InvocationContext] = None,
    adapter: Optional[str] = None,
    ttl_seconds: Optional[int] = None,
) -> Dict[str, Any]:
    init_environment_tables()
    resolved = resolve_commit(branch, commit_sha)
    adapter_name = _validate_adapter_name(adapter or os.environ.get("ALICE_ENV_ADAPTER", "local"))
    ttl = _resolve_ttl_seconds(adapter_name, ttl_seconds)
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
        "adapter": adapter_name,
        "ttl_seconds": ttl,
        "expires_at": (_now() + ttl) if ttl else None,
        "cloud_resource_id": None,
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
             runtime_port, data_namespace, owner_id, adapter, ttl_seconds, expires_at,
             cloud_resource_id, created_at, updated_at, started_at, stopped_at,
             deleted_at, error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            tuple(item.values()),
        )
        conn.commit()
    finally:
        conn.close()

    try:
        provisioned = _adapter_for(item).provision()
    except Exception as exc:
        _transition("CREATING", "FAILED")
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
        _record_trace(item, "CREATE_ENVIRONMENT", "FAILED", context=context, error=str(exc))
        raise
    item.update(provisioned)

    _transition("CREATING", "STOPPED")
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE environments SET status = ?, cloud_resource_id = ?, updated_at = ? WHERE environment_id = ?",
            ("STOPPED", item.get("cloud_resource_id"), _now(), environment_id),
        )
        conn.commit()
    finally:
        conn.close()
    item["status"] = "STOPPED"
    _record_trace(item, "CREATE_ENVIRONMENT", "SUCCESS", context=context)
    return item


def cleanup_expired_environments(
    owner_id: Optional[str] = None, *, context: Optional[InvocationContext] = None
) -> list[str]:
    """Delete environments past their TTL. Safe to call opportunistically."""
    init_environment_tables()
    now = _now()
    conn = get_conn()
    try:
        if owner_id:
            rows = conn.execute(
                """
                SELECT environment_id FROM environments
                 WHERE expires_at IS NOT NULL AND expires_at <= ? AND status != 'DELETING'
                   AND (owner_id = ? OR owner_id IS NULL)
                """,
                (now, owner_id),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT environment_id FROM environments
                 WHERE expires_at IS NOT NULL AND expires_at <= ? AND status != 'DELETING'
                """,
                (now,),
            ).fetchall()
    finally:
        conn.close()

    removed = []
    for row in rows:
        environment_id = row["environment_id"] if hasattr(row, "keys") else row[0]
        try:
            delete_environment(environment_id, owner_id, context=context)
            removed.append(environment_id)
        except (KeyError, ValueError):
            continue
    return removed


def list_environments(owner_id: Optional[str] = None) -> list[Dict[str, Any]]:
    init_environment_tables()
    cleanup_expired_environments(owner_id)
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


def start_environment(
    environment_id: str, owner_id: Optional[str] = None, *, context=None
) -> Dict[str, Any]:
    item = _require(environment_id, owner_id)
    _transition(item["status"], "RUNNING")
    try:
        thread_id = _adapter_for(item).start().get("runtime_thread_id")
        conn = get_conn()
        try:
            conn.execute(
                """
                UPDATE environments
                   SET status = ?, runtime_pid = NULL, runtime_port = NULL,
                       started_at = ?, updated_at = ?, error = NULL
                 WHERE environment_id = ?
                """,
                ("RUNNING", _now(), _now(), environment_id),
            )
            conn.commit()
        finally:
            conn.close()
        item.update(
            {
                "status": "RUNNING",
                "runtime_pid": None,
                "runtime_port": None,
                "runtime_thread_id": thread_id,
                "started_at": _now(),
                "error": None,
            }
        )
        _record_trace(
            item,
            "START_ENVIRONMENT",
            "SUCCESS",
            context=context,
            extra={"runtime_thread_id": thread_id},
        )
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


def stop_environment(
    environment_id: str, owner_id: Optional[str] = None, *, context=None
) -> Dict[str, Any]:
    item = _require(environment_id, owner_id)
    _transition(item["status"], "STOPPED")
    _adapter_for(item).stop()
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE environments SET status = ?, runtime_pid = NULL, updated_at = ?, stopped_at = ? WHERE environment_id = ?",
            ("STOPPED", _now(), _now(), environment_id),
        )
        conn.commit()
    finally:
        conn.close()
    item.update(
        {
            "status": "STOPPED",
            "runtime_pid": None,
            "runtime_port": None,
            "runtime_thread_id": None,
            "stopped_at": _now(),
        }
    )
    _record_trace(item, "STOP_ENVIRONMENT", "SUCCESS", context=context)
    return item


def restart_environment(
    environment_id: str, owner_id: Optional[str] = None, *, context=None
) -> Dict[str, Any]:
    item = _require(environment_id, owner_id)
    if item["status"] == "RUNNING":
        stop_environment(environment_id, owner_id, context=context)
    return start_environment(environment_id, owner_id, context=context)


def delete_environment(
    environment_id: str, owner_id: Optional[str] = None, *, context=None
) -> Dict[str, Any]:
    item = _require(environment_id, owner_id)
    if item["status"] == "RUNNING":
        stop_environment(environment_id, owner_id, context=context)
        item = _require(environment_id, owner_id)
    _transition(item["status"], "DELETING")
    _adapter_for(item).remove()
    item.update({"status": "DELETING", "deleted_at": _now()})
    _record_trace(item, "DELETE_ENVIRONMENT", "SUCCESS", context=context)
    conn = get_conn()
    try:
        conn.execute("DELETE FROM environments WHERE environment_id = ?", (environment_id,))
        conn.commit()
    finally:
        conn.close()
    return item


def execute_environment(
    environment_id: str,
    command: str,
    owner_id: Optional[str] = None,
    *,
    timeout_seconds: float = 30.0,
    context: Optional[InvocationContext] = None,
) -> Dict[str, Any]:
    """Run one command inside a RUNNING environment, local or Cloud.ru."""
    item = _require(environment_id, owner_id)
    if item["status"] != "RUNNING":
        raise ValueError(f"environment is not running: {item['status']}")
    try:
        result = _adapter_for(item).execute(command, timeout_seconds=timeout_seconds)
    except Exception as exc:
        _record_trace(item, "EXECUTE_ENVIRONMENT", "FAILED", context=context, error=str(exc))
        raise
    _record_trace(
        item,
        "EXECUTE_ENVIRONMENT",
        "SUCCESS" if result.get("success") else "FAILED",
        context=context,
        extra={"exit_code": result.get("exit_code")},
    )
    return result
