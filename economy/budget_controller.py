"""Monthly cloud budget guard: cloud spend classified from provider billing data."""

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, Decimal
from decimal import InvalidOperation as DecimalInvalidOperation
from enum import Enum
from typing import Any

from economy.host import bound


class BudgetError(Exception):
    """Base class for budget errors."""


class InvalidOperation(BudgetError):
    pass


def _money(value: Any) -> Decimal:
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0:
            raise InvalidOperation("amount must be a finite non-negative decimal")
        # A float's text can carry binary noise ("0.30000000000000004"). Rounding that off first keeps
        # 0.1 + 0.2 at 0.30, while a real sub-cent cost still rounds up below.
        amount = amount.quantize(Decimal("0.000000001"))
        # Round up so a sub-cent cost is never silently dropped from the ledger.
        return amount.quantize(Decimal("0.01"), rounding=ROUND_CEILING)
    except (DecimalInvalidOperation, ValueError, TypeError) as exc:
        raise InvalidOperation("amount must be a valid decimal") from exc


TraceSink = Callable[[dict[str, Any]], None]
Clock = Callable[[], datetime]


# Provider and transport failures that mean "billing unavailable", not a bug.
def _billing_outage_errors() -> tuple[type[BaseException], ...]:
    return (bound.CloudProviderError, OSError)


class CloudSpendStatus(str, Enum):
    OK = "ok"
    WARN = "warn"
    BLOCK = "block"
    UNCONFIGURED = "unconfigured"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class CloudBudgetLimits:
    """Monthly cloud spend limit checked against provider billing data."""

    monthly_limit: Decimal | None
    warn_ratio: Decimal = Decimal("0.80")
    currency: str = "RUB"

    def __post_init__(self) -> None:
        if self.monthly_limit is not None:
            object.__setattr__(self, "monthly_limit", _money(self.monthly_limit))
        ratio = Decimal(str(self.warn_ratio))
        if not Decimal(0) < ratio <= Decimal(1):
            raise InvalidOperation("warn_ratio must be in (0, 1]")
        object.__setattr__(self, "warn_ratio", ratio)
        object.__setattr__(self, "currency", self.currency.strip().upper())

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> CloudBudgetLimits:

        source = dict(os.environ) if env is None else env
        raw_limit = str(source.get("CLOUDRU_MONTHLY_BUDGET", "")).strip()
        raw_ratio = str(source.get("CLOUDRU_BUDGET_WARN_RATIO", "")).strip() or "0.80"
        currency = str(source.get("CLOUDRU_BILLING_CURRENCY", "")).strip() or "RUB"
        try:
            warn_ratio = Decimal(raw_ratio)
        except DecimalInvalidOperation as exc:
            raise ValueError("CLOUDRU_BUDGET_WARN_RATIO must be a number") from exc
        # NaN parses, but ordering it raises decimal.InvalidOperation in __post_init__.
        if warn_ratio.is_nan():
            raise ValueError("CLOUDRU_BUDGET_WARN_RATIO must be a number")
        return cls(_money(raw_limit) if raw_limit else None, warn_ratio, currency)


def evaluate_cloud_spend(spent: Any, limits: CloudBudgetLimits) -> dict[str, Any]:
    """Classify current-period cloud spend against a monthly limit."""
    if limits.monthly_limit is None:
        return {
            "status": CloudSpendStatus.UNCONFIGURED.value,
            "currency": limits.currency,
        }
    if spent is None:
        return {
            "status": CloudSpendStatus.UNKNOWN.value,
            "limit": str(limits.monthly_limit),
            "currency": limits.currency,
        }
    amount = _money(spent)
    limit = limits.monthly_limit
    if amount >= limit:
        status = CloudSpendStatus.BLOCK
    elif amount >= (limit * limits.warn_ratio).quantize(Decimal("0.01")):
        status = CloudSpendStatus.WARN
    else:
        status = CloudSpendStatus.OK
    return {
        "status": status.value,
        "spent": str(amount),
        "limit": str(limit),
        "remaining": str(max(Decimal("0.00"), limit - amount)),
        "ratio": str((amount / limit).quantize(Decimal("0.0001")))
        if limit > 0
        else None,
        "currency": limits.currency,
    }


class CloudBudgetGuard:
    """Report cloud spend against the monthly limit from real billing data.

    A billing outage turns the status into ``unknown``. The status is only reported:
    no operation is refused because of it.
    """

    def __init__(
        self,
        provider: Any,
        limits: CloudBudgetLimits | None = None,
        *,
        period: str | None = None,
        cache_seconds: int = 300,
        clock: Clock | None = None,
        trace_sink: TraceSink | None = None,
    ) -> None:
        self._provider = provider
        self.limits = limits or CloudBudgetLimits.from_env()
        self._period = period
        self._cache_seconds = max(0, int(cache_seconds))
        self._clock = clock or (lambda: datetime.now(UTC))
        self._trace_sink = trace_sink
        self._cached: dict[str, Any] | None = None
        self._cached_at: datetime | None = None
        self._lock = threading.Lock()

    def status(self, *, refresh: bool = False) -> dict[str, Any]:
        with self._lock:
            now = self._clock()
            period = self._period or now.strftime("%Y-%m")
            if (
                not refresh
                and self._cached is not None
                and self._cached_at is not None
                and self._cached.get("period") == period
                and now - self._cached_at < timedelta(seconds=self._cache_seconds)
            ):
                result = dict(self._cached)
                self._emit_if_not_ok(result)
                return result
            result = self._fetch(period)
            result["period"] = period
            result["checked_at"] = now.isoformat()
            self._cached, self._cached_at = result, now
            self._emit_if_not_ok(result)
            return dict(result)

    def _fetch(self, period: str) -> dict[str, Any]:
        if self.limits.monthly_limit is None:
            return evaluate_cloud_spend(None, self.limits)
        try:
            summary = self._provider.costs_summary(period=period, group_by=None)
        except _billing_outage_errors() as exc:
            # Billing outages must not break cloud operations; anything else surfaces.
            result = evaluate_cloud_spend(None, self.limits)
            result["error"] = type(exc).__name__
            return result
        try:
            result = evaluate_cloud_spend(summary.get("total_cost"), self.limits)
        except BudgetError:
            result = evaluate_cloud_spend(None, self.limits)
            result["error"] = "invalid_total"
        if (
            summary.get("complete") is False
            and result["status"] == CloudSpendStatus.OK.value
        ):
            # A partial sum is only a lower bound: it can show a block, never an all-clear.
            result["status"] = CloudSpendStatus.UNKNOWN.value
            result["error"] = "incomplete_billing_rows"
        currency = str(summary.get("currency") or "").upper()
        if currency and currency != self.limits.currency:
            result = evaluate_cloud_spend(None, self.limits)
            result["error"] = "currency_mismatch"
            result["billing_currency"] = currency
        result["provider"] = summary.get("provider")
        return result

    def _emit_if_not_ok(self, result: dict[str, Any]) -> None:
        if result["status"] not in {
            CloudSpendStatus.OK.value,
            CloudSpendStatus.UNCONFIGURED.value,
        }:
            self._emit("cloud_budget_" + result["status"], result)

    def _emit(self, event_type: str, data: dict[str, Any]) -> None:
        if self._trace_sink is not None:
            self._trace_sink({"event": event_type, **data})
