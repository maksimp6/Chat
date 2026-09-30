"""Exact-head task context cache with selective evidence reuse."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import ContextSlice, EvidenceVersion, TaskPacket, TaskScope


@dataclass(frozen=True)
class CacheLookup:
    """Result of one context-cache lookup."""

    status: str
    cache_key: str
    packet: TaskPacket | None = None
    scope: TaskScope | None = None
    evidence: EvidenceVersion | None = None
    reusable_slices: tuple[ContextSlice, ...] = ()
    stale_components: tuple[str, ...] = ()
    saved_source_bytes: int = 0
    saved_input_tokens: int = 0

    def __post_init__(self) -> None:
        if self.status not in {"hit", "partial", "miss"}:
            raise ValueError("cache status must be hit, partial, or miss")

    def as_trace_payload(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "cache_key": self.cache_key,
            "reused_slices": [item.name for item in self.reusable_slices],
            "stale_components": list(self.stale_components),
            "saved_source_bytes": self.saved_source_bytes,
            "saved_input_tokens": self.saved_input_tokens,
        }


class TaskContextCache:
    """In-process deterministic cache keyed by exact GitHub/task provenance.

    Persistence is intentionally deferred to the shared memory slice (#578).
    This layer establishes cache-key, invalidation, and telemetry semantics without
    introducing a second source of truth beside GitHub.
    """

    def __init__(self) -> None:
        self._entries: dict[str, TaskPacket] = {}
        self._latest_by_scope: dict[str, str] = {}

    def put(self, packet: TaskPacket) -> str:
        cache_key = packet.cache_key()
        self._entries[cache_key] = packet
        self._latest_by_scope[packet.scope.scope_key()] = cache_key
        return cache_key

    def lookup(self, scope: TaskScope, evidence: EvidenceVersion) -> CacheLookup:
        probe = TaskPacket(
            scope=scope,
            evidence=evidence,
            objective="cache lookup",
            expected_deliverable="cache lookup",
        )
        cache_key = probe.cache_key()
        exact = self._entries.get(cache_key)
        if exact is not None:
            source_bytes, input_tokens = self._usage(exact.slices)
            return CacheLookup(
                status="hit",
                cache_key=cache_key,
                packet=exact,
                scope=scope,
                evidence=evidence,
                reusable_slices=exact.slices,
                saved_source_bytes=source_bytes,
                saved_input_tokens=input_tokens,
            )

        previous_key = self._latest_by_scope.get(scope.scope_key())
        if previous_key is None:
            return CacheLookup(
                status="miss",
                cache_key=cache_key,
                scope=scope,
                evidence=evidence,
            )

        previous = self._entries.get(previous_key)
        if previous is None:
            return CacheLookup(
                status="miss",
                cache_key=cache_key,
                scope=scope,
                evidence=evidence,
            )

        stale_components = previous.evidence.changed_components(evidence)
        reusable = tuple(item for item in previous.slices if item.reusable_when(stale_components))
        if not reusable:
            return CacheLookup(
                status="miss",
                cache_key=cache_key,
                scope=scope,
                evidence=evidence,
                stale_components=stale_components,
            )

        source_bytes, input_tokens = self._usage(reusable)
        return CacheLookup(
            status="partial",
            cache_key=cache_key,
            scope=scope,
            evidence=evidence,
            reusable_slices=reusable,
            stale_components=stale_components,
            saved_source_bytes=source_bytes,
            saved_input_tokens=input_tokens,
        )

    def invalidate_scope(self, scope: TaskScope) -> int:
        """Invalidate all exact-evidence entries for one exact GitHub/task scope."""
        scope_key = scope.scope_key()
        keys = [
            key for key, packet in self._entries.items() if packet.scope.scope_key() == scope_key
        ]
        for key in keys:
            self._entries.pop(key, None)
        self._latest_by_scope.pop(scope_key, None)
        return len(keys)

    def __len__(self) -> int:
        return len(self._entries)

    @staticmethod
    def _usage(slices: tuple[ContextSlice, ...]) -> tuple[int, int]:
        return (
            sum(item.source_bytes for item in slices),
            sum(item.input_tokens for item in slices),
        )


def record_context_cache_lookup(
    trace: Any,
    lookup: CacheLookup,
    scope: TaskScope,
) -> dict[str, Any]:
    """Record bounded cache telemetry on the existing ExecutionTrace surface."""
    entry = {
        **lookup.as_trace_payload(),
        "repository": scope.repository,
        "work_item": scope.work_item,
        "base_sha": scope.base_sha,
        "head_sha": scope.head_sha,
        "role": scope.role,
    }
    trace.trace.setdefault("context_cache_operations", []).append(entry)
    trace.add_event(
        "context_cache_lookup",
        {
            "status": lookup.status,
            "work_item": scope.work_item,
            "role": scope.role,
            "saved_source_bytes": lookup.saved_source_bytes,
            "saved_input_tokens": lookup.saved_input_tokens,
            "stale_components": list(lookup.stale_components),
        },
    )
    return entry
