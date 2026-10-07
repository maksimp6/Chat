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
    """In-memory safety gate; provider billing remains external evidence."""

    def __init__(self, *, max_rub: Decimal, max_window_seconds: int) -> None:
        if max_rub <= 0:
            raise ValueError("max_rub must be positive")
        if isinstance(max_window_seconds, bool) or max_window_seconds <= 0:
            raise ValueError("max_window_seconds must be positive")
        self.max_rub = max_rub
        self.max_window_seconds = max_window_seconds
        self.action_on_limit = "deny"
        self._finished = False

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
        if predicted_rub > self.max_rub:
            raise BudgetExceeded("gross run budget exceeded")
        if elapsed_seconds >= self.max_window_seconds:
            raise BudgetExceeded("paid acceptance window expired")

    def finish(self, outcome: str) -> CleanupDecision:
        self._finished = True
        return CleanupDecision(
            cleanup_required=True,
            target_min_instances=0,
            acceptance_passed=outcome == "success",
        )

    def record_cleanup(self, *, success: bool) -> CleanupDecision:
        return CleanupDecision(
            cleanup_required=not success,
            target_min_instances=0,
            acceptance_passed=success and not self._finished,
            reason=None if success else "cleanup_failed",
        )
