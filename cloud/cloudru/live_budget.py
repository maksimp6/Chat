"""Fail-closed budget policy for one explicitly authorized live acceptance run."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


class BudgetExceeded(RuntimeError):
    """The current paid acceptance window cannot authorize another action."""


@dataclass(frozen=True)
class CleanupDecision:
    cleanup_required: bool
    target_min_instances: int
    acceptance_passed: bool
    reason: str | None = None


class LiveAcceptanceBudget:
    """In-memory safety gate; provider billing remains external evidence.

    Every authorized action's predicted cost is added to the run total, so many small
    actions cannot together exceed ``max_rub``.
    """

    def __init__(self, *, max_rub: Decimal, max_window_seconds: int) -> None:
        if max_rub <= 0:
            raise ValueError("max_rub must be positive")
        if isinstance(max_window_seconds, bool) or max_window_seconds <= 0:
            raise ValueError("max_window_seconds must be positive")
        self.max_rub = max_rub
        self.max_window_seconds = max_window_seconds
        self.action_on_limit = "deny"
        self._committed = Decimal("0")
        self._finished = False
        self._outcome: str | None = None

    @property
    def committed_rub(self) -> Decimal:
        return self._committed

    def authorize(
        self,
        *,
        predicted_rub: Decimal,
        elapsed_seconds: int,
        free_tier_remaining_rub: Decimal = Decimal("0"),
    ) -> None:
        del free_tier_remaining_rub
        if self._finished:
            raise BudgetExceeded("acceptance run is closed")
        if not isinstance(predicted_rub, Decimal) or not predicted_rub.is_finite():
            raise ValueError("predicted_rub must be a finite decimal")
        if predicted_rub < 0:
            raise ValueError("predicted_rub must be non-negative")
        if self._committed + predicted_rub > self.max_rub:
            raise BudgetExceeded("gross run budget exceeded")
        if elapsed_seconds >= self.max_window_seconds:
            raise BudgetExceeded("paid acceptance window expired")
        self._committed += predicted_rub

    def finish(self, outcome: str) -> CleanupDecision:
        self._finished = True
        self._outcome = outcome
        return CleanupDecision(
            cleanup_required=True,
            target_min_instances=0,
            acceptance_passed=outcome == "success",
        )

    def record_cleanup(self, *, success: bool) -> CleanupDecision:
        return CleanupDecision(
            cleanup_required=not success,
            target_min_instances=0,
            acceptance_passed=success and self._outcome == "success",
            reason=None if success else "cleanup_failed",
        )


__all__ = ["BudgetExceeded", "CleanupDecision", "LiveAcceptanceBudget"]
