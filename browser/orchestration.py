"""Deterministic parallel browser-role DAG orchestration.

The scheduler coordinates browser work without introducing a second tool executor.
Workers receive a BrowserRoleTask and are expected to call the existing runtime /
UniversalToolExecutor boundary.
"""

from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
import json
import time
from typing import Any, Callable, Mapping

from .capabilities import BROWSER_ACTIONS


READ_ACTIONS = frozenset({"navigate", "inspect", "screenshot", "assert_state"})
WRITE_ACTIONS = frozenset({"click", "fill"})
ROLE_ACTIONS = {
    "observer": READ_ACTIONS,
    "extractor": frozenset({"inspect", "screenshot", "assert_state"}),
    "verifier": frozenset({"inspect", "screenshot", "assert_state"}),
    "mouse": frozenset({"click"}),
    "keyboard": frozenset({"fill"}),
}


@dataclass(frozen=True)
class BrowserTaskBudget:
    max_seconds: float = 30.0
    max_tokens: int | None = None
    max_output_chars: int = 8000

    def __post_init__(self) -> None:
        if self.max_seconds <= 0:
            raise ValueError("max_seconds must be > 0")
        if self.max_tokens is not None and self.max_tokens < 0:
            raise ValueError("max_tokens must be >= 0")
        if self.max_output_chars <= 0:
            raise ValueError("max_output_chars must be > 0")


@dataclass(frozen=True)
class BrowserRoleTask:
    task_id: str
    role: str
    session_id: str
    capability: str
    action: str
    target: str
    value: str | None = None
    dependencies: tuple[str, ...] = ()
    budget: BrowserTaskBudget = field(default_factory=BrowserTaskBudget)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.task_id.strip():
            raise ValueError("task_id is required")
        if self.role not in ROLE_ACTIONS:
            raise ValueError(f"unsupported browser role: {self.role}")
        if not self.session_id.strip():
            raise ValueError("session_id is required")
        if self.action not in BROWSER_ACTIONS:
            raise ValueError(f"unsupported browser action: {self.action}")
        if self.action not in ROLE_ACTIONS[self.role]:
            raise ValueError(f"role {self.role} cannot execute action {self.action}")
        if not self.target.strip():
            raise ValueError("target is required")
        if self.task_id in self.dependencies:
            raise ValueError("task cannot depend on itself")

    @property
    def is_write(self) -> bool:
        return self.action in WRITE_ACTIONS


@dataclass(frozen=True)
class BrowserDagBudget:
    max_parallel_workers: int = 4
    max_total_tasks: int = 64
    max_total_tokens: int | None = None

    def __post_init__(self) -> None:
        if self.max_parallel_workers < 1:
            raise ValueError("max_parallel_workers must be >= 1")
        if self.max_total_tasks < 1:
            raise ValueError("max_total_tasks must be >= 1")
        if self.max_total_tokens is not None and self.max_total_tokens < 0:
            raise ValueError("max_total_tokens must be >= 0")


@dataclass(frozen=True)
class BrowserTaskResult:
    task_id: str
    status: str
    role: str
    session_id: str
    data: Any = None
    error: str | None = None
    consumed_tokens: int = 0
    output_chars: int = 0
    duration_ms: float = 0.0

    def to_mapping(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "role": self.role,
            "session_id": self.session_id,
            "data": self.data,
            "error": self.error,
            "consumed_tokens": self.consumed_tokens,
            "output_chars": self.output_chars,
            "duration_ms": self.duration_ms,
        }


@dataclass(frozen=True)
class BrowserDagResult:
    status: str
    tasks: Mapping[str, BrowserTaskResult]
    consumed_tokens: int
    duration_ms: float

    def to_mapping(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "consumed_tokens": self.consumed_tokens,
            "duration_ms": self.duration_ms,
            "tasks": {task_id: result.to_mapping() for task_id, result in self.tasks.items()},
        }


BrowserTaskWorker = Callable[[BrowserRoleTask], Any]


def _serialized_size(value: Any) -> tuple[str, int]:
    try:
        serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        serialized = str(value)
    return serialized, len(serialized)


def _compact_output(value: Any, max_chars: int) -> tuple[Any, int]:
    serialized, length = _serialized_size(value)
    if length <= max_chars:
        return value, length
    return {
        "truncated": True,
        "preview": serialized[:max_chars],
        "original_chars": length,
    }, max_chars


def _extract_tokens(raw_result: Any) -> int:
    if not isinstance(raw_result, Mapping):
        return 0
    metadata = raw_result.get("metadata")
    if isinstance(metadata, Mapping):
        for key in ("consumed_tokens", "total_tokens", "token_usage"):
            value = metadata.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                return value
    usage = raw_result.get("usage")
    if isinstance(usage, Mapping):
        value = usage.get("total_tokens")
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    return 0


def _extract_data(raw_result: Any) -> Any:
    if isinstance(raw_result, Mapping) and "success" in raw_result:
        if raw_result.get("success") is False:
            raise RuntimeError(str(raw_result.get("error") or "browser worker failed"))
        if "data" in raw_result:
            return raw_result.get("data")
    return raw_result


class BrowserRoleDag:
    """Execute a validated browser task DAG with session-aware parallelism."""

    def __init__(
        self,
        tasks: list[BrowserRoleTask] | tuple[BrowserRoleTask, ...],
        worker: BrowserTaskWorker,
        *,
        budget: BrowserDagBudget | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not callable(worker):
            raise TypeError("worker must be callable")
        self.tasks = tuple(tasks)
        self.worker = worker
        self.budget = budget or BrowserDagBudget()
        self.clock = clock
        self._task_map = self._validate()

    def _validate(self) -> dict[str, BrowserRoleTask]:
        if len(self.tasks) > self.budget.max_total_tasks:
            raise ValueError("task count exceeds max_total_tasks")

        task_map: dict[str, BrowserRoleTask] = {}
        for task in self.tasks:
            if task.task_id in task_map:
                raise ValueError(f"duplicate task_id: {task.task_id}")
            task_map[task.task_id] = task

        for task in self.tasks:
            missing = [dep for dep in task.dependencies if dep not in task_map]
            if missing:
                raise ValueError(
                    f"task {task.task_id} has missing dependencies: {', '.join(sorted(missing))}"
                )

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(task_id: str) -> None:
            if task_id in visited:
                return
            if task_id in visiting:
                raise ValueError("browser task graph contains a cycle")
            visiting.add(task_id)
            for dependency in task_map[task_id].dependencies:
                visit(dependency)
            visiting.remove(task_id)
            visited.add(task_id)

        for task_id in task_map:
            visit(task_id)
        return task_map

    @staticmethod
    def _session_can_start(
        task: BrowserRoleTask,
        running_tasks: Mapping[str, BrowserRoleTask],
    ) -> bool:
        same_session = [
            running
            for running in running_tasks.values()
            if running.session_id == task.session_id
        ]
        if not same_session:
            return True
        if task.is_write:
            return False
        return not any(running.is_write for running in same_session)

    def _run_task(self, task: BrowserRoleTask) -> BrowserTaskResult:
        started = self.clock()
        try:
            raw_result = self.worker(task)
            consumed_tokens = _extract_tokens(raw_result)
            data = _extract_data(raw_result)
            duration = max(0.0, self.clock() - started)

            if duration > task.budget.max_seconds:
                return BrowserTaskResult(
                    task.task_id,
                    "failed",
                    task.role,
                    task.session_id,
                    error="task time budget exceeded",
                    consumed_tokens=consumed_tokens,
                    duration_ms=round(duration * 1000, 2),
                )
            if (
                task.budget.max_tokens is not None
                and consumed_tokens > task.budget.max_tokens
            ):
                return BrowserTaskResult(
                    task.task_id,
                    "failed",
                    task.role,
                    task.session_id,
                    error="task token budget exceeded",
                    consumed_tokens=consumed_tokens,
                    duration_ms=round(duration * 1000, 2),
                )

            compacted, output_chars = _compact_output(data, task.budget.max_output_chars)
            return BrowserTaskResult(
                task.task_id,
                "succeeded",
                task.role,
                task.session_id,
                data=compacted,
                consumed_tokens=consumed_tokens,
                output_chars=output_chars,
                duration_ms=round(duration * 1000, 2),
            )
        except Exception as exc:
            duration = max(0.0, self.clock() - started)
            return BrowserTaskResult(
                task.task_id,
                "failed",
                task.role,
                task.session_id,
                error=f"{type(exc).__name__}: {exc}",
                duration_ms=round(duration * 1000, 2),
            )

    def run(self) -> BrowserDagResult:
        started = self.clock()
        results: dict[str, BrowserTaskResult] = {}
        pending = set(self._task_map)
        running: dict[Future[BrowserTaskResult], BrowserRoleTask] = {}
        total_tokens = 0

        with ThreadPoolExecutor(max_workers=self.budget.max_parallel_workers) as pool:
            while pending or running:
                changed = True
                while changed:
                    changed = False
                    for task_id in sorted(tuple(pending)):
                        task = self._task_map[task_id]
                        dependency_results = [results.get(dep) for dep in task.dependencies]
                        if any(
                            result is not None
                            and result.status in {"failed", "blocked", "cancelled"}
                            for result in dependency_results
                        ):
                            results[task_id] = BrowserTaskResult(
                                task_id,
                                "blocked",
                                task.role,
                                task.session_id,
                                error="dependency failed",
                            )
                            pending.remove(task_id)
                            changed = True

                available_slots = self.budget.max_parallel_workers - len(running)
                if available_slots > 0:
                    running_tasks = {task.task_id: task for task in running.values()}
                    ready = []
                    for task_id in sorted(pending):
                        task = self._task_map[task_id]
                        if not all(
                            dep in results and results[dep].status == "succeeded"
                            for dep in task.dependencies
                        ):
                            continue
                        if not self._session_can_start(task, running_tasks):
                            continue
                        ready.append(task)

                    for task in ready[:available_slots]:
                        future = pool.submit(self._run_task, task)
                        running[future] = task
                        pending.remove(task.task_id)
                        running_tasks[task.task_id] = task

                if not running:
                    if pending:
                        for task_id in sorted(pending):
                            task = self._task_map[task_id]
                            results[task_id] = BrowserTaskResult(
                                task_id,
                                "blocked",
                                task.role,
                                task.session_id,
                                error="scheduler could not make progress",
                            )
                        pending.clear()
                    break

                done, _ = wait(tuple(running), return_when=FIRST_COMPLETED)
                for future in done:
                    task = running.pop(future)
                    result = future.result()
                    total_tokens += result.consumed_tokens

                    if (
                        self.budget.max_total_tokens is not None
                        and total_tokens > self.budget.max_total_tokens
                    ):
                        result = BrowserTaskResult(
                            result.task_id,
                            "failed",
                            result.role,
                            result.session_id,
                            data=result.data,
                            error="DAG token budget exceeded",
                            consumed_tokens=result.consumed_tokens,
                            output_chars=result.output_chars,
                            duration_ms=result.duration_ms,
                        )
                    results[task.task_id] = result

        duration = max(0.0, self.clock() - started)
        statuses = {result.status for result in results.values()}
        if not results:
            status = "succeeded"
        elif statuses == {"succeeded"}:
            status = "succeeded"
        elif "failed" in statuses:
            status = "failed"
        else:
            status = "blocked"

        return BrowserDagResult(
            status=status,
            tasks=results,
            consumed_tokens=total_tokens,
            duration_ms=round(duration * 1000, 2),
        )


__all__ = [
    "BrowserDagBudget",
    "BrowserDagResult",
    "BrowserRoleDag",
    "BrowserRoleTask",
    "BrowserTaskBudget",
    "BrowserTaskResult",
    "READ_ACTIONS",
    "ROLE_ACTIONS",
    "WRITE_ACTIONS",
]
