"""Deterministic REAL/DEMO budget controller for department-level spending.

The controller is deliberately independent of any gambling strategy. It only
enforces accounting, limits, authorization boundaries, and audit events.
REAL and DEMO ledgers are separate and DEMO value is never convertible to REAL.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Callable, Dict, Optional
import uuid


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
    limits: BudgetLimits = field(
        default_factory=lambda: BudgetLimits(
            Decimal("0"), Decimal("0"), Decimal("0")
        )
    )

    def __post_init__(self) -> None:
        self.allocated = _money(self.allocated)
        self.spent = _money(self.spent)
        self.reserved = _money(self.reserved)
        self.currency = self.currency.upper()

    @property
    def available(self) -> Decimal:
        return self.allocated - self.spent - self.reserved

    @property
    def status(self) -> BudgetStatus:
        if self.available <= 0:
            return BudgetStatus.EXHAUSTED
        return BudgetStatus.ACTIVE


TraceSink = Callable[[Dict[str, Any]], None]


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
    ) -> None:
        if real.account_type is not AccountType.REAL:
            raise InvalidOperation("real account must have account_type REAL")
        if demo.account_type is not AccountType.DEMO:
            raise InvalidOperation("demo account must have account_type DEMO")
        if real.currency != demo.currency:
            raise InvalidOperation("REAL and DEMO accounts must use the same currency code")
        self.budget_id = budget_id
        self.accounts = {
            AccountType.REAL: real,
            AccountType.DEMO: demo,
        }
        self.active_account = AccountType.REAL
        self.fallback_to_demo = fallback_to_demo
        self._trace_sink = trace_sink
        self._emit(
            "budget_initialized",
            account_type=self.active_account.value,
            currency=real.currency,
        )

    def account(self, account_type: AccountType) -> BudgetAccount:
        return self.accounts[AccountType(account_type)]

    def snapshot(self) -> Dict[str, Any]:
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
                    "available": str(account.available),
                    "status": account.status.value,
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
        self._require_actor(actor)
        account = self.account(account_type)
        amount = _money(amount)
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
        policy_check: Callable[[AccountType, Decimal], bool],
    ) -> Dict[str, Any]:
        self._require_actor(requested_by)
        amount = _money(amount)
        if not policy_check(AccountType(account_type), amount):
            self._emit(
                "replenishment_denied",
                account_type=AccountType(account_type).value,
                currency=self.account(account_type).currency,
                amount=str(amount),
                actor=requested_by,
            )
            raise AuthorizationRequired("policy denied replenishment")

        return self.allocate(account_type, amount, actor="policy-approved:" + requested_by)

    def reserve(self, amount: Any, *, account_type: Optional[AccountType] = None) -> Dict[str, Any]:
        account_type = AccountType(account_type or self.active_account)
        account = self.account(account_type)
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

    def spend(self, amount: Any, *, account_type: Optional[AccountType] = None) -> Dict[str, Any]:
        account_type = AccountType(account_type or self.active_account)
        account = self.account(account_type)
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

        before = account.available
        account.spent += amount
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

    def release_reservation(
        self, amount: Any, *, account_type: Optional[AccountType] = None
    ) -> Dict[str, Any]:
        account_type = AccountType(account_type or self.active_account)
        account = self.account(account_type)
        amount = _money(amount)
        if amount > account.reserved:
            raise InvalidOperation("cannot release more than reserved")
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
        account_type = AccountType(account_type or self.active_account)
        account = self.account(account_type)
        amount = _money(amount)
        if amount > account.spent:
            raise InvalidOperation("cannot refund more than spent")
        before = account.available
        account.spent -= amount
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
        if self.active_account is AccountType.DEMO:
            return self.snapshot()
        self.active_account = AccountType.DEMO
        self._emit(
            "mode_switch",
            account_type=AccountType.DEMO.value,
            currency=self.account(AccountType.DEMO).currency,
            amount="0.00",
            reason=reason,
        )
        return self.snapshot()

    def convert_demo_to_real(self, amount: Any) -> None:
        _money(amount)
        raise DemoConversionDenied("DEMO funds have no monetary value and cannot convert to REAL")

    def _check_limits(self, account: BudgetAccount, amount: Decimal) -> None:
        if amount > account.limits.max_single_operation:
            raise BudgetLimitExceeded("single-operation limit exceeded")
        if account.spent + amount > account.limits.max_total_loss:
            raise BudgetLimitExceeded("total-loss limit exceeded")
        # daily loss is deliberately enforced by the same deterministic ceiling
        # until a period ledger is introduced in a later slice.
        if account.spent + amount > account.limits.max_daily_loss:
            raise BudgetLimitExceeded("daily-loss limit exceeded")

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
