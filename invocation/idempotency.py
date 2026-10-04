"""Bounded, in-process idempotency store for effects.

A repeated key returns the first successful result instead of running the
effect again. A failed effect releases its key so the caller can retry.
The store is per process: it deduplicates retries and double submits within
one worker, not across a multi-process deployment.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Callable

MAX_KEY_LENGTH = 200


class IdempotencyInProgress(Exception):
    """The same key is already executing."""


@dataclass(frozen=True)
class IdempotentOutcome:
    value: Any
    replayed: bool


class IdempotencyStore:
    def __init__(
        self,
        max_entries: int = 1024,
        ttl_seconds: float = 3600.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        if max_entries < 1 or ttl_seconds <= 0:
            raise ValueError("max_entries and ttl_seconds must be positive")
        self._max_entries = max_entries
        self._ttl = ttl_seconds
        self._clock = clock
        self._done: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._running: set[str] = set()
        self._lock = threading.Lock()

    def run(
        self,
        key: str,
        effect: Callable[[], Any],
        is_success: Callable[[Any], bool] = lambda _value: True,
    ) -> IdempotentOutcome:
        """Run ``effect`` once per key; repeat calls replay the stored result."""
        validate_key(key)
        with self._lock:
            self._expire()
            if key in self._done:
                return IdempotentOutcome(self._done[key][1], replayed=True)
            if key in self._running:
                raise IdempotencyInProgress(key)
            self._running.add(key)
        try:
            value = effect()
        except BaseException:
            with self._lock:
                self._running.discard(key)
            raise
        with self._lock:
            self._running.discard(key)
            if is_success(value):
                self._done[key] = (self._clock(), value)
                while len(self._done) > self._max_entries:
                    self._done.popitem(last=False)
        return IdempotentOutcome(value, replayed=False)

    def _expire(self) -> None:
        cutoff = self._clock() - self._ttl
        while self._done:
            oldest_key, (stored_at, _value) = next(iter(self._done.items()))
            if stored_at > cutoff:
                return
            del self._done[oldest_key]


def validate_key(key: str) -> None:
    if not isinstance(key, str) or not key.strip() or len(key) > MAX_KEY_LENGTH:
        raise ValueError(f"idempotency key must be a non-empty string up to {MAX_KEY_LENGTH} chars")


__all__ = [
    "IdempotencyInProgress",
    "IdempotencyStore",
    "IdempotentOutcome",
    "MAX_KEY_LENGTH",
    "validate_key",
]
