"""Branch-aware application environment control plane.

The manager owns immutable environment metadata and a small local runtime adapter.
It never mutates an environment's source after creation: a new commit gets a new
environment id. Production preview infrastructure may use the same metadata
contract through its deployment workflow.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import socket
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

logger = logging.getLogger(__name__)

# Runtime threads live in one process, but their ownership is recorded in the
# database: each RUNNING row names the instance hosting it and a heartbeat. A
# restarted or additional instance only reclaims rows whose owner is gone.
INSTANCE_ID = os.environ.get("ALICE_INSTANCE_ID") or (
    f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
)
_HEARTBEAT_SECONDS = 15.0

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
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                started_at INTEGER,
                stopped_at INTEGER,
                deleted_at INTEGER,
                error TEXT,
                runtime_instance TEXT,
                runtime_heartbeat_at INTEGER
            )
            """
        )
        existing_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(environments)").fetchall()
        }
        for column in ("runtime_instance TEXT", "runtime_heartbeat_at INTEGER"):
            if column.split()[0] not in existing_columns:
                conn.execute(f"ALTER TABLE environments ADD COLUMN {column}")
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
        conn.execute("CREATE INDEX IF NOT EXISTS idx_environments_owner ON environments(owner_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_environments_status ON environments(status)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_environment_events_env ON environment_events(environment_id)"
        )
        running_rows = conn.execute(
            """
            SELECT environment_id, runtime_instance, runtime_heartbeat_at
              FROM environments WHERE status = ?
            """,
            ("RUNNING",),
        ).fetchall()
        with _RUNTIME_WORKERS_LOCK:
            active_runtime_ids = set(_RUNTIME_WORKERS)
        for row in running_rows:
            runtime_id = row["environment_id"]
            if runtime_id in active_runtime_ids or _owned_by_live_instance(row):
                continue
            conn.execute(
                """
                UPDATE environments
                   SET status = ?, runtime_pid = NULL, runtime_port = NULL,
                       runtime_instance = NULL, runtime_heartbeat_at = NULL,
                       updated_at = ?, stopped_at = COALESCE(stopped_at, ?)
                 WHERE environment_id = ? AND status = ?
                   AND COALESCE(runtime_instance, '') = ?
                   AND COALESCE(runtime_heartbeat_at, -1) = ?
                """,
                # Only reclaim the ownership observed above: a heartbeat that
                # lands in between makes this a no-op.
                (
                    "STOPPED",
                    _now(),
                    _now(),
                    runtime_id,
                    "RUNNING",
                    row["runtime_instance"] or "",
                    -1 if row["runtime_heartbeat_at"] is None else row["runtime_heartbeat_at"],
                ),
            )
        conn.commit()
    finally:
        conn.close()


def _runtime_lease_seconds() -> float:
    return float(os.environ.get("ALICE_RUNTIME_LEASE_SECONDS", "").strip() or 60)


def _owned_by_live_instance(row) -> bool:
    """True when another instance hosts the runtime and its heartbeat is fresh."""
    instance = row["runtime_instance"]
    heartbeat = row["runtime_heartbeat_at"]
    if not instance or instance == INSTANCE_ID or heartbeat is None:
        return False
    return _now() - int(heartbeat) < _runtime_lease_seconds()


def _record_heartbeat(environment_id: str) -> None:
    conn = get_conn()
    try:
        conn.execute(
            """
            UPDATE environments SET runtime_heartbeat_at = ?
             WHERE environment_id = ? AND runtime_instance = ?
            """,
            (_now(), environment_id, INSTANCE_ID),
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
        self.owned_in_db = False
        self._heartbeat_stop = threading.Event()
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            name=f"alice-runtime-heartbeat-{self.runtime_id[:8]}",
            daemon=True,
        )

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

    def _stop_heartbeat(self) -> None:
        self._heartbeat_stop.set()
        if self._heartbeat_thread.is_alive():
            self._heartbeat_thread.join(timeout=5)

    def start(self) -> int:
        # Heartbeats start before the (possibly slow) worktree preparation so
        # the ownership lease reserved by start_environment cannot lapse.
        self._heartbeat_thread.start()
        try:
            return self._start()
        except Exception:
            self._stop_heartbeat()
            raise

    def _start(self) -> int:
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
            # Keep the heartbeat so no other instance reclaims a live runtime.
            raise RuntimeError("environment runtime thread did not stop")
        self._stop_heartbeat()
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

    def _heartbeat(self) -> None:
        try:
            _record_heartbeat(self.runtime_id)
        except Exception:
            logger.exception("Runtime %s heartbeat failed", self.runtime_id)

    def _heartbeat_loop(self) -> None:
        # Separate from the job thread so a long operation cannot let the lease lapse.
        while not self._heartbeat_stop.wait(_HEARTBEAT_SECONDS):
            self._heartbeat()

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


def _local_runtime(environment_id: str) -> "EnvironmentRuntime":
    with _RUNTIME_WORKERS_LOCK:
        runtime = _RUNTIME_WORKERS.get(environment_id)
    item = _get(environment_id)
    if runtime is not None:
        # Runtimes started by start_environment are owned through their row; a
        # missing, stopped or re-owned row means this instance lost ownership.
        lost = runtime.owned_in_db and (
            item is None or item["status"] != "RUNNING" or item["runtime_instance"] != INSTANCE_ID
        )
        if not lost:
            return runtime
        # Another instance reclaimed or stopped this runtime: stop serving it here.
        runtime.stop()
        raise RuntimeError("environment runtime is no longer owned by this instance")
    if item and item["status"] == "RUNNING" and _owned_by_live_instance(item):
        raise RuntimeError(
            f"environment runtime is running on another instance: {item['runtime_instance']}"
        )
    raise RuntimeError("environment runtime is not running")


def dispatch_environment_operation(
    environment_id: str,
    operation: str,
    payload: Optional[Dict[str, Any]] = None,
    *,
    timeout: float = 30.0,
) -> Any:
    runtime = _local_runtime(environment_id)
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
    runtime = _local_runtime(environment_id)
    _RUNTIME_DISPATCHER.context(environment_id)
    return runtime.submit_http(payload, timeout=timeout)


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


def start_environment(
    environment_id: str, owner_id: Optional[str] = None, *, context=None
) -> Dict[str, Any]:
    item = _require(environment_id, owner_id)
    _transition(item["status"], "RUNNING")
    # Reserve ownership atomically before starting the thread, so two instances
    # that both saw STOPPED cannot both start the runtime.
    conn = get_conn()
    try:
        reserved = conn.execute(
            """
            UPDATE environments
               SET status = ?, runtime_pid = NULL, runtime_port = NULL,
                   runtime_instance = ?, runtime_heartbeat_at = ?,
                   started_at = ?, updated_at = ?, error = NULL
             WHERE environment_id = ? AND status = ?
            """,
            ("RUNNING", INSTANCE_ID, _now(), _now(), _now(), environment_id, item["status"]),
        ).rowcount
        conn.commit()
    finally:
        conn.close()
    if reserved != 1:
        raise ValueError("environment is already being started by another instance")
    runtime = EnvironmentRuntime(item)
    runtime.owned_in_db = True
    try:
        thread_id = runtime.start()
        item.update(
            {
                "status": "RUNNING",
                "runtime_pid": None,
                "runtime_port": None,
                "runtime_thread_id": thread_id,
                "runtime_instance": INSTANCE_ID,
                "runtime_heartbeat_at": _now(),
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
                """
                UPDATE environments
                   SET status = ?, error = ?, runtime_instance = NULL,
                       runtime_heartbeat_at = NULL, updated_at = ?
                 WHERE environment_id = ?
                """,
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
    with _RUNTIME_WORKERS_LOCK:
        runtime = _RUNTIME_WORKERS.get(environment_id)
    if runtime is None and _owned_by_live_instance(item):
        raise ValueError(
            f"environment runtime is running on another instance: {item['runtime_instance']}"
        )
    if runtime is not None:
        runtime.stop()
    conn = get_conn()
    try:
        # Conditional on the ownership read above, so a runtime another
        # instance took over in the meantime is never marked stopped.
        stopped = conn.execute(
            """
            UPDATE environments
               SET status = ?, runtime_pid = NULL, runtime_instance = NULL,
                   runtime_heartbeat_at = NULL, updated_at = ?, stopped_at = ?
             WHERE environment_id = ? AND COALESCE(runtime_instance, '') = ?
            """,
            ("STOPPED", _now(), _now(), environment_id, item.get("runtime_instance") or ""),
        ).rowcount
        conn.commit()
    finally:
        conn.close()
    if stopped != 1:
        raise ValueError("environment ownership changed while stopping; retry")
    item.update(
        {
            "status": "STOPPED",
            "runtime_pid": None,
            "runtime_port": None,
            "runtime_thread_id": None,
            "runtime_instance": None,
            "runtime_heartbeat_at": None,
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
