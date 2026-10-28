"""Branch-aware application environment control plane.

The manager owns immutable environment metadata and a small local runtime adapter.
It never mutates an environment's source after creation: a new commit gets a new
environment id. Production preview infrastructure may use the same metadata
contract through its deployment workflow.
"""

from __future__ import annotations

import functools
import logging
import os
import queue
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from remote_runtime.host import (
    bind_runtime_request,
    bound,
    close_memory_clients,
    create_invocation_trace,
    get_store,
    init_runtime_db,
)

ENV_STATUSES = ("CREATING", "RUNNING", "STOPPED", "FAILED", "DELETING")

_runtime_dispatcher_instance = None
_runtime_dispatcher_lock = threading.Lock()
_RUNTIME_WORKERS: dict[str, "EnvironmentRuntime"] = {}
_RUNTIME_WORKERS_LOCK = threading.RLock()
_RUNTIME_STOP = object()
_STREAM_IDLE_SECONDS = 60.0
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
    return runtime.loader.invoke(
        str(payload.get("operation") or ""), payload.get("payload")
    )


def _runtime_dispatcher() -> Any:
    """The process dispatcher for preview runtimes, created on first use."""
    global _runtime_dispatcher_instance
    with _runtime_dispatcher_lock:
        if _runtime_dispatcher_instance is None:
            _runtime_dispatcher_instance = bound.RuntimeDispatcher()
            _runtime_dispatcher_instance.register_operation(
                "revision.invoke", _invoke_revision_runtime
            )
        return _runtime_dispatcher_instance


def _repo_root() -> Path:
    return Path(os.environ.get("LOGOS_ENV_REPO_ROOT") or os.getcwd()).resolve()


logger = logging.getLogger("logos.remote_runtime.environments")


class GitUnavailable(RuntimeError):
    """git could not run on this server: it is missing, or it did not finish in time."""


def _run_git(*args: str, cwd: Path | None = None) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(cwd or _repo_root()),
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        # A server fault, not a bad ref in the request, so it is not reported as a ValueError.
        logger.error("git %s could not run: %s", args[0], type(exc).__name__)
        raise GitUnavailable("git is unavailable") from exc
    if result.returncode:
        # git's stderr names server paths, so it is logged here and not returned to the caller.
        logger.info("git %s failed: %s", args[0], result.stderr.strip())
        raise ValueError("git command failed")
    return result.stdout.strip()


def resolve_commit(branch: str, commit_sha: str | None = None) -> str:
    """Resolve a branch/ref to one immutable commit without shell evaluation."""
    branch_value = str(branch or "").strip()
    value = str(commit_sha or branch_value).strip()
    if not branch_value:
        raise ValueError("branch is required")
    if not value:
        raise ValueError("commit is required")
    if value.startswith("-") or branch_value.startswith("-"):
        # A leading dash would be read by git as an option.
        raise ValueError("invalid git ref")
    if any(ch in value for ch in ("\x00", "\n", "\r")) or any(
        ch in branch_value for ch in ("\x00", "\n", "\r")
    ):
        raise ValueError("invalid git ref")
    try:
        resolved = _run_git("rev-parse", "--verify", f"{value}^{{commit}}")
        branch_resolved = (
            _run_git("rev-parse", "--verify", f"{branch_value}^{{commit}}")
            if commit_sha
            else None
        )
    except ValueError as exc:
        raise ValueError(f"git ref is not available: {value}") from exc
    if branch_resolved is not None:
        try:
            _run_git("merge-base", "--is-ancestor", resolved, branch_resolved)
        except ValueError as exc:
            # The commit exists but is not on this branch, so the reason must not be reported as a missing ref.
            raise ValueError("commit is not reachable from branch") from exc
    if len(resolved) != 40 or any(
        ch not in "0123456789abcdef" for ch in resolved.lower()
    ):
        raise ValueError("git did not return a commit SHA")
    return resolved


def prepare_environment_records() -> None:
    store = get_store()
    with store.transaction() as tx:
        # Read the workers under the store lock: a start registers its worker before it records
        # RUNNING, so a running environment is never marked STOPPED from an older snapshot.
        with _RUNTIME_WORKERS_LOCK:
            active = set(_RUNTIME_WORKERS)
        for key, row in store.items(prefix="environment/record/").items():
            if row["status"] == "RUNNING" and row["environment_id"] not in active:
                tx.set(
                    key,
                    {
                        **row,
                        "status": "STOPPED",
                        "runtime_pid": None,
                        "runtime_port": None,
                        "updated_at": int(time.time()),
                        "stopped_at": row["stopped_at"] or int(time.time()),
                    },
                )


# Former name, kept for existing callers.
init_environment_tables = prepare_environment_records


def _transition(current: str, target: str) -> None:
    if target not in _ALLOWED_TRANSITIONS.get(current, set()):
        raise ValueError(f"invalid environment transition: {current} -> {target}")


def _runtime_thread_id(environment_id: str) -> int | None:
    with _RUNTIME_WORKERS_LOCK:
        worker = _RUNTIME_WORKERS.get(environment_id)
    return worker.thread_id if worker else None


def _row_to_dict(row) -> dict[str, Any]:
    item = dict(row)
    item["runtime_thread_id"] = _runtime_thread_id(item["environment_id"])
    return item


def _get(environment_id: str) -> dict[str, Any] | None:
    row = get_store().get("environment/record/" + environment_id)
    return _row_to_dict(row) if row is not None else None


def _require(environment_id: str, owner_id: str | None = None) -> dict[str, Any]:
    del owner_id
    item = _get(environment_id)
    if not item:
        raise KeyError("environment_not_found")
    return item


def _public_url(environment_id: str) -> str:
    base = os.environ.get("LOGOS_ENV_PUBLIC_BASE_URL", "").rstrip("/")
    if base:
        return f"{base}/environments/{environment_id}/"
    return f"/environments/{environment_id}/"


def _namespace(environment_id: str) -> str:
    return f"env_{environment_id.replace('-', '_')}"


def _record_trace(
    environment: dict[str, Any],
    operation: str,
    status: str,
    *,
    context: Any | None = None,
    error: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    trace = create_invocation_trace(context) if context else bound.ExecutionTrace()
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
    with get_store().transaction() as tx:
        event_id = str(uuid.uuid4())
        tx.set(
            "environment/event/" + event_id,
            {
                "event_id": event_id,
                "environment_id": environment["environment_id"],
                "operation": operation,
                "status": status,
                "trace_id": trace.trace_id,
                "trace_json": trace_json,
                "created_at": int(time.time()),
            },
        )
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
                try:
                    event = self._events.get(timeout=_STREAM_IDLE_SECONDS)
                except queue.Empty as exc:
                    raise TimeoutError(
                        "environment runtime HTTP stream stalled"
                    ) from exc
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
    the runtime itself stays inside the Logos Python process. Runtime work
    is executed only after RuntimeDispatcher binds the environment scope.
    """

    def __init__(self, environment: dict[str, Any]):
        self.environment = environment
        self.runtime_id = environment["environment_id"]
        self.root = Path(
            os.environ.get("LOGOS_ENV_RUNTIME_ROOT") or ".logos-environments"
        ).resolve()
        self.worktree = self.root / self.runtime_id / "source"
        self.data_dir = self.root / self.runtime_id / "data"
        self.loader = bound.RuntimeLoader(
            runtime_id=self.runtime_id,
            revision=environment["commit_sha"],
            source_root=self.worktree,
            dispatcher=_runtime_dispatcher(),
        )
        self._jobs: queue.Queue = queue.Queue()
        self._thread = threading.Thread(
            target=self._run,
            name=f"logos-runtime-{self.runtime_id[:8]}",
            daemon=True,
        )
        self._stream_lock = threading.RLock()
        self._active_streams: dict[threading.Event, queue.Queue] = {}

    @property
    def thread_id(self) -> int | None:
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
            _runtime_dispatcher().register_runtime(
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
            _runtime_dispatcher().unregister_runtime(self.runtime_id)

            close_memory_clients(self.data_dir / "logos.memory")
            raise

    def submit(
        self,
        operation: str,
        payload: dict[str, Any] | None = None,
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
        payload: dict[str, Any],
        *,
        timeout: float = 30.0,
    ) -> RuntimeHTTPStream:
        if not self._thread.is_alive():
            raise RuntimeError("environment runtime is not running")
        events: queue.Queue = queue.Queue(maxsize=8)
        cancel = threading.Event()
        with self._stream_lock:
            self._active_streams[cancel] = events
        # Each HTTP request gets its own thread, so one slow response cannot block the environment's
        # other calls.
        threading.Thread(
            target=self._run_http,
            args=(dict(payload), events, cancel),
            name=f"logos-runtime-http-{self.runtime_id[:8]}",
            daemon=True,
        ).start()
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
        _runtime_dispatcher().unregister_runtime(self.runtime_id)

        close_memory_clients(self.data_dir / "logos.memory")

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

    def _run_http(
        self, payload: dict[str, Any], events: queue.Queue, cancel: threading.Event
    ) -> None:
        source = None
        try:
            base_path = str(payload.get("base_path") or "")
            with bind_runtime_request(
                self.runtime_id,
                base_path,
                data_root=str(self.data_dir),
            ):
                source = _runtime_dispatcher().dispatch(
                    self.runtime_id, "http.request", payload
                )
                status_code = int(source["status_code"])
                headers = list(source.get("headers") or [])
                if not _stream_put(events, ("start", status_code, headers), cancel):
                    return
                for chunk in source.get("body") or ():
                    if cancel.is_set():
                        break
                    if isinstance(chunk, str):
                        chunk = chunk.encode("utf-8")
                    if chunk and not _stream_put(
                        events, ("chunk", bytes(chunk)), cancel
                    ):
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
                        value = _runtime_dispatcher().dispatch(
                            self.runtime_id, operation, payload
                        )
                    result.put(("result", value))
                except Exception as exc:
                    result.put(("error", exc))
                continue


def register_runtime_operation(name: str, handler) -> None:
    """Register a dispatcher operation available to managed runtime threads."""
    _runtime_dispatcher().register_operation(name, handler)


def authorize_environment_runtime(
    environment_id: str,
    owner_id: str | None,
) -> None:
    """Resolve an existing environment without an owner authorization gate."""
    item = _get(environment_id)
    try:
        _runtime_dispatcher().authorize(environment_id, owner_id)
        return
    except bound.RuntimeNotFound:
        # Stopped environments intentionally have no dispatcher context. The
        # gateway distinguishes a stopped environment (503) from a missing one (404).
        if not item:
            raise


def dispatch_environment_operation(
    environment_id: str,
    operation: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout: float = 30.0,
) -> Any:
    with _RUNTIME_WORKERS_LOCK:
        runtime = _RUNTIME_WORKERS.get(environment_id)
    if runtime is None:
        raise RuntimeError("environment runtime is not running")
    _runtime_dispatcher().context(environment_id)
    return runtime.submit(operation, payload, timeout=timeout)


def dispatch_environment_revision(
    environment_id: str,
    operation: str,
    payload: dict[str, Any] | None = None,
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
    payload: dict[str, Any],
    *,
    timeout: float = 30.0,
) -> RuntimeHTTPStream:
    with _RUNTIME_WORKERS_LOCK:
        runtime = _RUNTIME_WORKERS.get(environment_id)
    if runtime is None:
        raise RuntimeError("environment runtime is not running")
    _runtime_dispatcher().context(environment_id)
    return runtime.submit_http(payload, timeout=timeout)


def create_environment(
    branch: str,
    commit_sha: str | None = None,
    owner_id: str | None = None,
    *,
    context: Any | None = None,
) -> dict[str, Any]:
    prepare_environment_records()
    resolved = resolve_commit(branch, commit_sha)
    environment_id = str(uuid.uuid4())
    item = {
        "environment_id": environment_id,
        "branch_name": branch.strip(),
        "commit_sha": resolved,
        "status": "STOPPED",
        "url": _public_url(environment_id),
        "runtime_pid": None,
        "runtime_port": None,
        "data_namespace": _namespace(environment_id),
        "owner_id": owner_id,
        "created_at": int(time.time()),
        "updated_at": int(time.time()),
        "started_at": None,
        "stopped_at": None,
        "deleted_at": None,
        "error": None,
    }
    with get_store().transaction() as tx:
        tx.set("environment/record/" + environment_id, item)
    _record_trace(item, "CREATE_ENVIRONMENT", "SUCCESS", context=context)
    return item


def list_environments(owner_id: str | None = None) -> list[dict[str, Any]]:
    del owner_id
    prepare_environment_records()
    rows = list(get_store().items(prefix="environment/record/").values())
    # Owner ids remain internal metadata rather than part of the listing.
    return [
        {key: value for key, value in _row_to_dict(row).items() if key != "owner_id"}
        for row in sorted(rows, key=lambda row: row["created_at"], reverse=True)
    ]


_ENVIRONMENT_LOCKS: dict[str, Any] = {}
_ENVIRONMENT_LOCKS_GUARD = threading.Lock()


def _serialize_by_environment(fn):
    """Run start, stop and delete one at a time per environment; status is read under the lock."""

    @functools.wraps(fn)
    def wrapper(environment_id: str, *args: Any, **kwargs: Any) -> Any:
        with _ENVIRONMENT_LOCKS_GUARD:
            # RLock: delete_environment calls stop_environment while holding the lock.
            lock = _ENVIRONMENT_LOCKS.setdefault(environment_id, threading.RLock())
        with lock:
            return fn(environment_id, *args, **kwargs)

    return wrapper


@_serialize_by_environment
def start_environment(
    environment_id: str, owner_id: str | None = None, *, context=None
) -> dict[str, Any]:
    item = _require(environment_id, owner_id)
    _transition(item["status"], "RUNNING")
    runtime = EnvironmentRuntime(item)
    try:
        thread_id = runtime.start()
        _persist_environment(
            environment_id,
            {
                "status": "RUNNING",
                "runtime_pid": None,
                "runtime_port": None,
                "started_at": int(time.time()),
                "error": None,
            },
        )
        item.update(
            {
                "status": "RUNNING",
                "runtime_pid": None,
                "runtime_port": None,
                "runtime_thread_id": thread_id,
                "started_at": int(time.time()),
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
        _persist_environment(environment_id, {"status": "FAILED", "error": str(exc)})
        item.update({"status": "FAILED", "error": str(exc)})
        _record_trace(
            item, "START_ENVIRONMENT", "FAILED", context=context, error=str(exc)
        )
        raise


@_serialize_by_environment
def stop_environment(
    environment_id: str, owner_id: str | None = None, *, context=None
) -> dict[str, Any]:
    item = _require(environment_id, owner_id)
    _transition(item["status"], "STOPPED")
    with _RUNTIME_WORKERS_LOCK:
        runtime = _RUNTIME_WORKERS.get(environment_id)
    if runtime is not None:
        try:
            runtime.stop()
        except RuntimeError as exc:
            # The worker did not stop, so the environment is not stopped. Record that instead of
            # leaving it RUNNING while its worker is still registered.
            _persist_environment(
                environment_id, {"status": "FAILED", "error": str(exc)}
            )
            raise
    _persist_environment(
        environment_id,
        {
            "status": "STOPPED",
            "runtime_pid": None,
            "runtime_port": None,
            "stopped_at": int(time.time()),
        },
    )
    item.update(
        {
            "status": "STOPPED",
            "runtime_pid": None,
            "runtime_port": None,
            "runtime_thread_id": None,
            "stopped_at": int(time.time()),
        }
    )
    _record_trace(item, "STOP_ENVIRONMENT", "SUCCESS", context=context)
    return item


def restart_environment(
    environment_id: str, owner_id: str | None = None, *, context=None
) -> dict[str, Any]:
    item = _require(environment_id, owner_id)
    if item["status"] == "RUNNING":
        stop_environment(environment_id, owner_id, context=context)
    return start_environment(environment_id, owner_id, context=context)


@_serialize_by_environment
def delete_environment(
    environment_id: str, owner_id: str | None = None, *, context=None
) -> dict[str, Any]:
    item = _require(environment_id, owner_id)
    with _RUNTIME_WORKERS_LOCK:
        runtime = _RUNTIME_WORKERS.get(environment_id)
    # A failed stop can leave a worker alive; finish stopping it before removing its data.
    if item["status"] == "RUNNING" or (
        runtime is not None and item["status"] == "FAILED"
    ):
        stop_environment(environment_id, owner_id, context=context)
        item = _require(environment_id, owner_id)
    elif runtime is not None:
        runtime.stop()
    _transition(item["status"], "DELETING")
    item.update({"status": "DELETING", "deleted_at": int(time.time())})
    _record_trace(item, "DELETE_ENVIRONMENT", "SUCCESS", context=context)
    # The record goes first: if the files were removed and the record then survived, the record
    # would claim an environment whose source and data are gone.
    with get_store().transaction() as tx:
        tx.delete("environment/record/" + environment_id)
    EnvironmentRuntime(item).remove()
    return item


def _persist_environment(environment_id: str, changes: dict[str, Any]) -> None:
    key = "environment/record/" + environment_id
    with get_store().transaction() as tx:
        old = tx.get(key)
        if old is None:
            raise KeyError("environment_not_found")
        tx.set(key, {**old, **changes, "updated_at": int(time.time())})
