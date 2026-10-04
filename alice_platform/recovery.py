"""Recovery primitives for bounded Alice Platform rollback."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List


@dataclass(frozen=True)
class Snapshot:
    timestamp: str
    size: str
    retention_days: int


@dataclass(frozen=True)
class RecoveryPolicy:
    max_attempts: int = 3

    def __post_init__(self) -> None:
        if self.max_attempts < 1 or self.max_attempts > 10:
            raise ValueError("max_attempts must be between 1 and 10")


def bounded_recovery(operation: Callable[[], bool], *, policy: RecoveryPolicy | None = None) -> int:
    """Run recovery until healthy, with a hard retry ceiling."""
    policy = policy or RecoveryPolicy()
    for attempt in range(1, policy.max_attempts + 1):
        if operation():
            return attempt
    raise RuntimeError("recovery attempts exhausted")


def list_snapshots(storage: str, lane: str, *, loader: Callable[[str, str], List[Snapshot]] | None = None) -> List[Snapshot]:
    if loader is None:
        raise RuntimeError("snapshot provider is not configured")
    return loader(storage, lane)


def restore_snapshot(
    storage: str,
    snapshot_timestamp: str,
    lane: str,
    approve: bool = False,
    *,
    restorer: Callable[[str, str, str], None] | None = None,
) -> None:
    if lane == "production" and not approve:
        raise PermissionError("production restore requires approval")
    if restorer is None:
        raise RuntimeError("snapshot provider is not configured")
    restorer(storage, snapshot_timestamp, lane)


def rollback_config(
    commit: str,
    lane: str,
    approve: bool = False,
    *,
    rollback: Callable[[str, str], None] | None = None,
) -> None:
    if lane == "production" and not approve:
        raise PermissionError("production rollback requires approval")
    if rollback is None:
        raise RuntimeError("config rollback provider is not configured")
    rollback(commit, lane)
