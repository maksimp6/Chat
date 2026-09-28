"""Deterministic REAL/DEMO budget controller for department-level spending.

This module is a financial-control primitive, not a gambling strategy. It keeps
REAL and DEMO ledgers separate and makes every state transition deterministic.
External policy/authorization remains authoritative for replenishment and
returning from DEMO to REAL.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
import threading
import uuid
from typing import Any, Callable, Dict, Optional, TYPE_CHECKING

from cloud.base import CloudProviderError

if TYPE_CHECKING:
    from budget_repository import BudgetRepository


class AccountType(str, Enum):
    REAL = "REAL"
    DEMO = "DEMO"


class BudgetStatus(str, Enum):
    ACTIVE = "ACTIVE"
    EXHAUSTED = "EXHAUSTED"
    SUSPENDED = "SUSPENDED"


class BudgetError(Exception):
    """Base class for deterministic budget-controller errors."""


class BudgetLimitExceeded(BudgetError):
    pass


class InsufficientFunds(BudgetError):
    pass


class AuthorizationRequired(BudgetError):
    pass


class InvalidOperation(BudgetError):
    pass


class DemoConversionDenied(BudgetError):
    pass


def _money(value: Any) -> Decimal:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise InvalidOperation("amount must be a valid decimal") from exc
    if amount < 0:
        raise InvalidOperation("amount must be non-negative")
    return amount.quantize(Decimal("0.01"))


@dataclass(frozen=True)
class BudgetLimits:
    max_single_operation: Decimal
    max_daily_loss: Decimal
    max_total_loss: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "max_single_operation", _money(self.max_single_operation))
        object.__setattr__(self, "max_daily_loss", _money(self.max_daily_loss))
        object.__setattr__(self, "max_total_loss", _money(self.max_total_loss))


@dataclass
class BudgetAccount:
    account_type: AccountType
    currency: str
    allocated: Decimal = Decimal("0")
    spent: Decimal = Decimal("0")
    reserved: Decimal = Decimal("0")
    won: Decimal = Decimal("0")
    loss_today: Decimal = Decimal("0")
    loss_period: str = ""
    limits: BudgetLimits = field(
        default_factory=lambda: BudgetLimits(Decimal("0"), Decimal("0"), Decimal("0"))
    )
    locked_until: Optional[datetime] = None

    def __post_init__(self) -> None:
        self.allocated = _money(self.allocated)
        self.spent = _money(self.spent)
        self.reserved = _money(self.reserved)
        self.won = _money(self.won)
        self.loss_today = _money(self.loss_today)
        self.loss_period = self.loss_period or datetime.now(timezone.utc).date().isoformat()
        self.currency = self.currency.upper()

    @property
    def available(self) -> Decimal:
        return self.allocated + self.won - self.spent - self.reserved

    def status(self, now: Optional[datetime] = None) -> BudgetStatus:
        now = now or datetime.now(timezone.utc)
        if self.locked_until is not None and now < self.locked_until:
            return BudgetStatus.SUSPENDED
        if self.available <= 0:
            return BudgetStatus.EXHAUSTED
        return BudgetStatus.ACTIVE


TraceSink = Callable[[Dict[str, Any]], None]
Clock = Callable[[], datetime]
PolicyCheck = Callable[[AccountType, Decimal], bool]


class BudgetController:
    """Deterministic controller for isolated REAL and DEMO budget accounts."""

    def __init__(
        self,
        budget_id: str,
        real: BudgetAccount,
        demo: BudgetAccount,
        *,
        trace_sink: Optional[TraceSink] = None,
        fallback_to_demo: bool = True,
        cooldown_seconds: int = 0,
        clock: Optional[Clock] = None,
        repository: Optional["BudgetRepository"] = None,
        persistence_actor: str = "budget-controller",
    ) -> None:
        if real.account_type is not AccountType.REAL:
            raise InvalidOperation("real account must have account_type REAL")
        if demo.account_type is not AccountType.DEMO:
            raise InvalidOperation("demo account must have account_type DEMO")
        if real.currency != demo.currency:
            raise InvalidOperation("REAL and DEMO accounts must use the same currency code")
        if cooldown_seconds < 0:
            raise InvalidOperation("cooldown_seconds must be non-negative")
        self.budget_id = budget_id
        self.accounts = {AccountType.REAL: real, AccountType.DEMO: demo}
        self.active_account = AccountType.REAL
        self.fallback_to_demo = fallback_to_demo
        self.cooldown_seconds = cooldown_seconds
        self._trace_sink = trace_sink
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._repository = repository
        self._persistence_actor = persistence_actor
        self._lock = threading.RLock()
        self._emit("budget_initialized", account_type="REAL", currency=real.currency)

    def account(self, account_type: AccountType) -> BudgetAccount:
        return self.accounts[AccountType(account_type)]

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            now = self._now()
            return {
                "budget_id": self.budget_id,
                "active_account": self.active_account.value,
                "accounts": {
                    kind.value: {
                        "account_type": account.account_type.value,
                        "currency": account.currency,
                        "allocated": str(account.allocated),
                        "spent": str(account.spent),
                        "reserved": str(account.reserved),
                        "won": str(account.won),
                        "available": str(account.available),
                        "loss_today": str(account.loss_today),
                        "loss_period": account.loss_period,
                        "status": account.status(now).value,
                        "locked_until": account.locked_until.isoformat()
                        if account.locked_until
                        else None,
                        "limits": {
                            "max_single_operation": str(account.limits.max_single_operation),
                            "max_daily_loss": str(account.limits.max_daily_loss),
                            "max_total_loss": str(account.limits.max_total_loss),
                        },
                    }
                    for kind, account in self.accounts.items()
                },
            }

    def allocate(self, account_type: AccountType, amount: Any, *, actor: str) -> Dict[str, Any]:
        with self._lock:
            self._require_actor(actor)
            account = self.account(account_type)
            amount = _money(amount)
            self._persist("ALLOCATE", account_type, amount, actor=actor)
            before = account.available
            account.allocated += amount
            self._emit(
                "allocation",
                account_type=account.account_type.value,
                currency=account.currency,
                amount=str(amount),
                balance_before=str(before),
                balance_after=str(account.available),
                actor=actor,
            )
            return self.snapshot()

    def request_replenishment(
        self,
        account_type: AccountType,
        amount: Any,
        *,
        requested_by: str,
        policy_check: PolicyCheck,
    ) -> Dict[str, Any]:
        with self._lock:
            self._require_actor(requested_by)
            account_type = AccountType(account_type)
            amount = _money(amount)
            self._ensure_not_locked(self.account(account_type))
            if not policy_check(account_type, amount):
                self._emit(
                    "replenishment_denied",
                    account_type=account_type.value,
                    currency=self.account(account_type).currency,
                    amount=str(amount),
                    actor=requested_by,
                )
                raise AuthorizationRequired("policy denied replenishment")
            return self.allocate(account_type, amount, actor="policy-approved:" + requested_by)

    def reserve(self, amount: Any, *, account_type: Optional[AccountType] = None) -> Dict[str, Any]:
        with self._lock:
            account_type = AccountType(account_type or self.active_account)
            account = self.account(account_type)
            self._ensure_not_locked(account)
            amount = _money(amount)
            self._check_limits(account, amount)
            if amount > account.available:
                if (
                    account_type is AccountType.REAL
                    and self.fallback_to_demo
                    and account.available <= 0
                ):
                    self.switch_to_demo(reason="real_budget_exhausted")
                    return self.reserve(amount, account_type=AccountType.DEMO)
                raise InsufficientFunds("insufficient available budget")

            self._persist("RESERVE", account_type, amount)
            before = account.available
            account.reserved += amount
            self._emit(
                "reservation",
                account_type=account_type.value,
                currency=account.currency,
                amount=str(amount),
                balance_before=str(before),
                balance_after=str(account.available),
            )
            return self.snapshot()

    def settle_reservation(
        self,
        amount: Any,
        *,
        account_type: Optional[AccountType] = None,
    ) -> Dict[str, Any]:
        """Convert an existing reservation into a spend atomically."""
        with self._lock:
            account_type = AccountType(account_type or self.active_account)
            account = self.account(account_type)
            amount = _money(amount)
            if amount > account.reserved:
                raise InvalidOperation("cannot settle more than reserved")
            self._persist("SETTLE", account_type, amount)
            before = account.available
            account.reserved -= amount
            account.spent += amount
            account.loss_today += amount
            self._apply_cooldown_if_needed(account)
            self._emit(
                "reservation_settled",
                account_type=account_type.value,
                currency=account.currency,
                amount=str(amount),
                balance_before=str(before),
                balance_after=str(account.available),
            )
            return self.snapshot()

    def spend(self, amount: Any, *, account_type: Optional[AccountType] = None) -> Dict[str, Any]:
        with self._lock:
            account_type = AccountType(account_type or self.active_account)
            account = self.account(account_type)
            self._ensure_not_locked(account)
            amount = _money(amount)
            self._check_limits(account, amount)
            if amount > account.available:
                if (
                    account_type is AccountType.REAL
                    and self.fallback_to_demo
                    and account.available <= 0
                ):
                    self.switch_to_demo(reason="real_budget_exhausted")
                    return self.spend(amount, account_type=AccountType.DEMO)
                raise InsufficientFunds("insufficient available budget")

            self._persist("SPEND", account_type, amount)
            before = account.available
            account.spent += amount
            account.loss_today += amount
            self._apply_cooldown_if_needed(account)
            self._emit(
                "spend",
                account_type=account_type.value,
                currency=account.currency,
                amount=str(amount),
                balance_before=str(before),
                balance_after=str(account.available),
            )
            if account.available <= 0:
                self._emit(
                    "budget_exhausted",
                    account_type=account_type.value,
                    currency=account.currency,
                    amount="0.00",
                    balance_before=str(account.available),
                    balance_after=str(account.available),
                )
            return self.snapshot()

    def record_win(
        self, amount: Any, *, account_type: Optional[AccountType] = None
    ) -> Dict[str, Any]:
        """Record a provider-confirmed win; LLMs cannot mint this value."""
        with self._lock:
            account_type = AccountType(account_type or self.active_account)
            account = self.account(account_type)
            amount = _money(amount)
            self._persist("WIN", account_type, amount)
            before = account.available
            account.won += amount
            self._emit(
                "win",
                account_type=account_type.value,
                currency=account.currency,
                amount=str(amount),
                balance_before=str(before),
                balance_after=str(account.available),
            )
            return self.snapshot()

    def release_reservation(
        self, amount: Any, *, account_type: Optional[AccountType] = None
    ) -> Dict[str, Any]:
        with self._lock:
            account_type = AccountType(account_type or self.active_account)
            account = self.account(account_type)
            amount = _money(amount)
            if amount > account.reserved:
                raise InvalidOperation("cannot release more than reserved")
            self._persist("RELEASE", account_type, amount)
            before = account.available
            account.reserved -= amount
            self._emit(
                "reservation_released",
                account_type=account_type.value,
                currency=account.currency,
                amount=str(amount),
                balance_before=str(before),
                balance_after=str(account.available),
            )
            return self.snapshot()

    def refund(self, amount: Any, *, account_type: Optional[AccountType] = None) -> Dict[str, Any]:
        with self._lock:
            account_type = AccountType(account_type or self.active_account)
            account = self.account(account_type)
            amount = _money(amount)
            if amount > account.spent:
                raise InvalidOperation("cannot refund more than spent")
            self._persist("REFUND", account_type, amount)
            before = account.available
            account.spent -= amount
            account.loss_today = max(Decimal("0.00"), account.loss_today - amount)
            self._emit(
                "refund",
                account_type=account_type.value,
                currency=account.currency,
                amount=str(amount),
                balance_before=str(before),
                balance_after=str(account.available),
            )
            return self.snapshot()

    def switch_to_demo(self, *, reason: str) -> Dict[str, Any]:
        with self._lock:
            if self.active_account is AccountType.DEMO:
                return self.snapshot()
            self.active_account = AccountType.DEMO
            self._emit(
                "mode_switch",
                account_type="DEMO",
                currency=self.account(AccountType.DEMO).currency,
                amount="0.00",
                reason=reason,
            )
            return self.snapshot()

    def activate_real(
        self,
        *,
        actor: str,
        authorization_check: Callable[[], bool],
    ) -> Dict[str, Any]:
        """Return DEMO -> REAL only after explicit external authorization."""
        with self._lock:
            self._require_actor(actor)
            if self.active_account is AccountType.REAL:
                return self.snapshot()
            if not authorization_check():
                self._emit("real_mode_denied", account_type="REAL", actor=actor)
                raise AuthorizationRequired("explicit authorization required for DEMO -> REAL")
            self.active_account = AccountType.REAL
            self._emit(
                "mode_switch", account_type="REAL", actor=actor, reason="manual_authorization"
            )
            return self.snapshot()

    def convert_demo_to_real(self, amount: Any) -> None:
        _money(amount)
        raise DemoConversionDenied("DEMO funds have no monetary value and cannot convert to REAL")

    def _persist(
        self,
        operation_type: str,
        account_type: AccountType,
        amount: Decimal,
        *,
        actor: Optional[str] = None,
    ) -> None:
        if self._repository is None:
            return
        self._repository.apply(
            self.budget_id,
            account_type.value,
            operation_type,
            amount,
            idempotency_key="budget-" + uuid.uuid4().hex,
            actor=actor or self._persistence_actor,
            cooldown_seconds=self.cooldown_seconds,
        )

    def _check_limits(self, account: BudgetAccount, amount: Decimal) -> None:
        self._roll_loss_period(account)
        if amount > account.limits.max_single_operation:
            raise BudgetLimitExceeded("single-operation limit exceeded")
        if account.spent + amount > account.limits.max_total_loss:
            raise BudgetLimitExceeded("total-loss limit exceeded")
        if account.loss_today + amount > account.limits.max_daily_loss:
            self._lock_account(account, reason="daily_loss_limit")
            raise BudgetLimitExceeded("daily-loss limit exceeded")

    def _roll_loss_period(self, account: BudgetAccount) -> None:
        today = self._now().date().isoformat()
        if account.loss_period != today:
            account.loss_period = today
            account.loss_today = Decimal("0.00")
            account.locked_until = None
            self._emit(
                "loss_period_reset",
                account_type=account.account_type.value,
                currency=account.currency,
                amount="0.00",
            )

    def _apply_cooldown_if_needed(self, account: BudgetAccount) -> None:
        if account.loss_today >= account.limits.max_daily_loss:
            self._lock_account(account, reason="daily_loss_limit")

    def _lock_account(self, account: BudgetAccount, *, reason: str) -> None:
        if self.cooldown_seconds <= 0:
            return
        account.locked_until = self._now() + timedelta(seconds=self.cooldown_seconds)
        self._emit(
            "cooldown_started",
            account_type=account.account_type.value,
            currency=account.currency,
            amount="0.00",
            locked_until=account.locked_until.isoformat(),
            reason=reason,
        )

    def _ensure_not_locked(self, account: BudgetAccount) -> None:
        self._roll_loss_period(account)
        if account.locked_until is not None and self._now() < account.locked_until:
            raise BudgetLimitExceeded("budget is in cooldown")
        if account.locked_until is not None and self._now() >= account.locked_until:
            account.locked_until = None
            self._emit(
                "cooldown_expired",
                account_type=account.account_type.value,
                currency=account.currency,
                amount="0.00",
            )

    def _now(self) -> datetime:
        current = self._clock()
        if current.tzinfo is None:
            return current.replace(tzinfo=timezone.utc)
        return current.astimezone(timezone.utc)

    @staticmethod
    def _require_actor(actor: str) -> None:
        if not isinstance(actor, str) or not actor.strip():
            raise AuthorizationRequired("authorized actor is required")

    def _emit(self, event_type: str, **data: Any) -> None:
        event = {
            "event": event_type,
            "trace_event_id": "budget_" + uuid.uuid4().hex,
            "budget_id": self.budget_id,
            **data,
        }
        if self._trace_sink is not None:
            self._trace_sink(event)


# Provider and transport failures that mean "billing unavailable", not a bug.
_BILLING_OUTAGE_ERRORS: tuple[type[BaseException], ...] = (CloudProviderError, OSError)


class CloudSpendStatus(str, Enum):
    OK = "ok"
    WARN = "warn"
    BLOCK = "block"
    UNCONFIGURED = "unconfigured"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class CloudBudgetLimits:
    """Monthly cloud spend limit checked against provider billing data."""

    monthly_limit: Optional[Decimal]
    warn_ratio: Decimal = Decimal("0.80")
    currency: str = "RUB"

    def __post_init__(self) -> None:
        if self.monthly_limit is not None:
            object.__setattr__(self, "monthly_limit", _money(self.monthly_limit))
        ratio = Decimal(str(self.warn_ratio))
        if not Decimal("0") < ratio <= Decimal("1"):
            raise InvalidOperation("warn_ratio must be in (0, 1]")
        object.__setattr__(self, "warn_ratio", ratio)
        object.__setattr__(self, "currency", self.currency.strip().upper())

    @classmethod
    def from_env(cls, env: Optional[Dict[str, str]] = None) -> "CloudBudgetLimits":
        import os

        source = os.environ if env is None else env
        raw_limit = str(source.get("CLOUDRU_MONTHLY_BUDGET", "")).strip()
        raw_ratio = str(source.get("CLOUDRU_BUDGET_WARN_RATIO", "")).strip() or "0.80"
        currency = str(source.get("CLOUDRU_BILLING_CURRENCY", "")).strip() or "RUB"
        return cls(_money(raw_limit) if raw_limit else None, Decimal(raw_ratio), currency)


def evaluate_cloud_spend(spent: Any, limits: CloudBudgetLimits) -> Dict[str, Any]:
    """Classify current-period cloud spend against a monthly limit."""
    if limits.monthly_limit is None:
        return {"status": CloudSpendStatus.UNCONFIGURED.value, "currency": limits.currency}
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
        "ratio": str((amount / limit).quantize(Decimal("0.0001"))) if limit > 0 else None,
        "currency": limits.currency,
    }


class CloudBudgetGuard:
    """Warn or block cost-creating cloud operations from real billing data.

    Billing outages never block operations: the status becomes ``unknown`` and
    the caller decides. Only a confirmed spend at or above the limit blocks.
    """

    def __init__(
        self,
        provider: Any,
        limits: Optional[CloudBudgetLimits] = None,
        *,
        period: Optional[str] = None,
        cache_seconds: int = 300,
        clock: Optional[Clock] = None,
        trace_sink: Optional[TraceSink] = None,
    ) -> None:
        self._provider = provider
        self.limits = limits or CloudBudgetLimits.from_env()
        self._period = period
        self._cache_seconds = max(0, int(cache_seconds))
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._trace_sink = trace_sink
        self._cached: Optional[Dict[str, Any]] = None
        self._cached_at: Optional[datetime] = None
        self._lock = threading.Lock()

    def status(self, *, refresh: bool = False) -> Dict[str, Any]:
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

    def _fetch(self, period: str) -> Dict[str, Any]:
        if self.limits.monthly_limit is None:
            return evaluate_cloud_spend(None, self.limits)
        try:
            summary = self._provider.costs_summary(period=period, group_by=None)
        except _BILLING_OUTAGE_ERRORS as exc:
            # Billing outages must not break cloud operations; anything else surfaces.
            result = evaluate_cloud_spend(None, self.limits)
            result["error"] = type(exc).__name__
            return result
        try:
            result = evaluate_cloud_spend(summary.get("total_cost"), self.limits)
        except BudgetError:
            result = evaluate_cloud_spend(None, self.limits)
            result["error"] = "invalid_total"
        currency = str(summary.get("currency") or "").upper()
        if currency and currency != self.limits.currency:
            result = evaluate_cloud_spend(None, self.limits)
            result["error"] = "currency_mismatch"
            result["billing_currency"] = currency
        result["provider"] = summary.get("provider")
        return result

    def _emit_if_not_ok(self, result: Dict[str, Any]) -> None:
        if result["status"] not in {CloudSpendStatus.OK.value, CloudSpendStatus.UNCONFIGURED.value}:
            self._emit("cloud_budget_" + result["status"], result)

    def enforce(self, operation: str) -> Dict[str, Any]:
        result = self.status()
        if result["status"] == CloudSpendStatus.BLOCK.value:
            raise BudgetLimitExceeded(
                f"cloud monthly budget exhausted: {result['spent']} of "
                f"{result['limit']} {result['currency']}; '{operation}' blocked"
            )
        return result

    def _emit(self, event_type: str, data: Dict[str, Any]) -> None:
        if self._trace_sink is not None:
            self._trace_sink({"event": event_type, **data})
