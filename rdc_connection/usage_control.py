"""Task-local limits for authorized RDC callbacks; no connector or credential bypass."""

from __future__ import annotations

from core.wikifunctions import trim_string

import math
import threading
import time
from collections.abc import Callable
from typing import Generic, TypeVar

T = TypeVar("T")


class CallLimitReached(RuntimeError):
    """The task has exhausted its approved external-call allowance."""


class ResultPending(RuntimeError):
    """The existing operation is pending; do not submit it again."""


def _validate_collection(
    operation_id: str, timeout_s: float, max_polls: int, initial_wait_s: float
) -> None:
    if not isinstance(operation_id, str) or not trim_string(operation_id):
        raise ValueError("original operation_id is required")
    if type(max_polls) is not int or not 1 <= max_polls <= 8:
        raise ValueError("max_polls must be an integer in 1..8")
    if not math.isfinite(timeout_s) or not 0 < timeout_s <= 300:
        raise ValueError("timeout must be in (0, 300] seconds")
    if not math.isfinite(initial_wait_s) or not 1 <= initial_wait_s <= 60:
        raise ValueError("initial wait must be in 1..60 seconds")


class RdcTask(Generic[T]):
    """Serialize one owner/device/invocation's authorized connector callbacks.

    Callbacks must retain normal policy/trace boundaries and transport timeouts.
    The cap is task-local, not an account-wide monthly quota implementation.
    """

    def __init__(
        self,
        *,
        owner_id: str,
        device_id: str,
        invocation_id: str,
        max_calls: int,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not all(
            isinstance(value, str) and trim_string(value)
            for value in (owner_id, device_id, invocation_id)
        ):
            raise ValueError("owner, device and invocation are required")
        if type(max_calls) is not int or not 1 <= max_calls <= 100:
            raise ValueError("max_calls must be an approved integer in 1..100")
        self._scope = (owner_id, device_id, invocation_id)
        self._max_calls = max_calls
        self._calls = 0
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.RLock()
        self._observation: tuple[str, float, T] | None = None
        self._completed: tuple[str, T] | None = None

    @property
    def calls_used(self) -> int:
        """Return reserved calls, including failures and ambiguous results."""
        with self._lock:
            return self._calls

    def call(self, callback: Callable[[], T], *, changes_device: bool = False) -> T:
        """Execute once; never retry a mutation or ambiguous transport failure."""
        with self._lock:
            if self._calls >= self._max_calls:
                raise CallLimitReached("RDC task call allowance exhausted")
            if changes_device:
                self._observation = None
            self._calls += 1
            return callback()

    def observe(self, observation_id: str, capture: Callable[[], T], *, max_age_s: float = 2) -> T:
        """Capture only on demand; coalesce the same short-lived observation.

        A new explicit observation ID always requests fresh evidence. Cached
        evidence must not be reused as the current screen for later actions.
        """
        if not isinstance(observation_id, str) or not trim_string(observation_id):
            raise ValueError("observation_id is required")
        if not math.isfinite(max_age_s) or not 0 <= max_age_s <= 5:
            raise ValueError("observation reuse must be in 0..5 seconds")
        with self._lock:
            cached = self._observation
            if cached is not None:
                key, captured_at, result = cached
                age = self._clock() - captured_at
                if key == observation_id and 0 <= age < max_age_s:
                    return result
            # Timestamp before capture: a slow capture cannot extend freshness.
            captured_at = self._clock()
            result = self.call(capture)
            self._observation = (observation_id, captured_at, result)
            return result

    def invalidate_observation(self) -> None:
        """Forget evidence after external input, focus, rotation or geometry change."""
        with self._lock:
            self._observation = None

    def collect(
        self,
        operation_id: str,
        current: T,
        read_existing: Callable[[], T],
        finished: Callable[[T], bool],
        *,
        timeout_s: float = 60,
        max_polls: int = 4,
        initial_wait_s: float = 5,
    ) -> T:
        """Read the existing PID/call_id with backoff; never restart the operation.

        Bind read_existing to the original device, PID/call_id, invocation and
        trace. A pending exception preserves that handle for explicit continuation.
        """
        _validate_collection(operation_id, timeout_s, max_polls, initial_wait_s)
        with self._lock:
            if self._completed is not None and self._completed[0] == operation_id:
                return self._completed[1]
            if finished(current):
                self._completed = (operation_id, current)
                return current
            deadline = self._clock() + timeout_s
            delay = initial_wait_s
            for _ in range(max_polls):
                if self._calls >= self._max_calls:
                    raise CallLimitReached("RDC task call allowance exhausted")
                remaining = deadline - self._clock()
                if remaining <= delay:
                    break
                self._sleep(delay)
                if self._clock() >= deadline:
                    break
                current = self.call(read_existing)
                if finished(current):
                    self._completed = (operation_id, current)
                    return current
                delay = min(delay * 2, 60)
            raise ResultPending("existing RDC operation remains pending; keep its handle")
