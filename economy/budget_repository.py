"""Native durable budget accounts and idempotent operations on Kristall."""

from __future__ import annotations
from datetime import datetime, timedelta, timezone
from decimal import ROUND_CEILING, Decimal
import json
import uuid
from typing import Any
from storage.db import get_store
from observability.trace_manager import get_current_trace


class BudgetPersistenceError(RuntimeError):
    pass


_FIELDS = ("allocated", "spent", "reserved", "won", "loss_today", "carried_spent")


def _key(budget_id: str, account_type: str) -> str:
    return "budget/account/" + json.dumps([budget_id, account_type], separators=(",", ":"))


def _decimal(value: Any) -> Decimal:
    amount = Decimal(str(value))
    if not amount.is_finite() or amount < 0:
        raise ValueError("amount must be finite and non-negative")
    # Round up, matching BudgetController: sub-cent spend is never dropped.
    return amount.quantize(Decimal("0.01"), rounding=ROUND_CEILING)


def _legacy_carried(row: dict[str, Any]) -> str:
    """Rows written before carried_spent existed: everything beyond today's loss is carried."""
    return str(max(Decimal("0"), Decimal(row["spent"]) - Decimal(row["loss_today"])))


class BudgetRepository:
    def configure(self, budget_id: str, account: Any) -> dict[str, Any]:
        """Initialize once; subsequent clients hydrate the authoritative account."""
        key = _key(budget_id, account.account_type.value)
        row = {
            "budget_id": budget_id,
            "account_type": account.account_type.value,
            "currency": account.currency,
            **{field: str(getattr(account, field)) for field in _FIELDS},
            "loss_period": account.loss_period,
            "locked_until": account.locked_until.isoformat() if account.locked_until else None,
            "limits": {
                field: str(getattr(account.limits, field))
                for field in ("max_single_operation", "max_daily_loss", "max_total_loss")
            },
            "version": 0,
        }
        with get_store().transaction() as tx:
            stored = tx.get(key)
            if stored is None:
                tx.set(key, row)
                stored = row
        stored.setdefault("carried_spent", _legacy_carried(stored))
        return stored

    def apply(
        self,
        budget_id: str,
        account_type: str,
        operation_type: str,
        amount: Any,
        *,
        idempotency_key: str | None = None,
        actor: str | None = None,
        cooldown_seconds: int = 0,
    ) -> dict[str, Any]:
        amount = _decimal(amount)
        if account_type not in {"REAL", "DEMO"} or cooldown_seconds < 0:
            raise ValueError("invalid account or cooldown")
        if operation_type == "ALLOCATE" and not actor:
            raise BudgetPersistenceError("allocation requires an authorized actor")
        store = get_store()
        account_key = _key(budget_id, account_type)
        replay_key = "budget/idempotency/" + json.dumps(
            [budget_id, idempotency_key], separators=(",", ":")
        )
        now = datetime.now(timezone.utc)
        with store.transaction() as tx:
            old = tx.get(replay_key) if idempotency_key else None
            if old is not None:
                if (old["account_type"], old["operation_type"], old["amount"]) != (
                    account_type,
                    operation_type,
                    str(amount),
                ):
                    raise BudgetPersistenceError("idempotency key conflicts with another operation")
                return {"operation_id": old["operation_id"], "status": "APPLIED"}
            row = tx.get(account_key)
            if row is None:
                raise BudgetPersistenceError("budget account not found")
            row.setdefault("carried_spent", _legacy_carried(row))
            balances = {field: Decimal(row[field]) for field in _FIELDS}
            if row["loss_period"] != now.date().isoformat():
                balances["loss_today"] = Decimal("0")
                balances["carried_spent"] = balances["spent"]
                row["locked_until"] = None
            _mutate(row, balances, operation_type, amount, now)
            if (
                operation_type in {"SPEND", "SETTLE"}
                and balances["loss_today"] >= Decimal(row["limits"]["max_daily_loss"])
                and cooldown_seconds
            ):
                row["locked_until"] = (now + timedelta(seconds=cooldown_seconds)).isoformat()
            operation_id = str(uuid.uuid4())
            operation = {
                "operation_id": operation_id,
                "budget_id": budget_id,
                "account_type": account_type,
                "operation_type": operation_type,
                "amount": str(amount),
                "actor": actor,
                "status": "APPLIED",
                "created_at": now.isoformat(),
            }
            tx.set(
                account_key,
                {
                    **row,
                    **{field: str(value) for field, value in balances.items()},
                    "loss_period": now.date().isoformat(),
                    "version": row["version"] + 1,
                    "updated_at": now.isoformat(),
                },
            )
            tx.set("budget/operation/" + operation_id, operation)
            if idempotency_key:
                tx.set(replay_key, operation)
        trace = get_current_trace()
        if trace is not None:
            trace.add_event(
                "budget_operation_persisted",
                {
                    "budget_id": budget_id,
                    "account_type": account_type,
                    "operation_type": operation_type,
                    "amount": str(amount),
                    "operation_id": operation_id,
                    "status": "APPLIED",
                    "idempotency_key": idempotency_key,
                },
            )
        return {"operation_id": operation_id, "status": "APPLIED"}


def _admit(
    row: dict[str, Any],
    balances: dict[str, Decimal],
    operation: str,
    amount: Decimal,
    now: datetime,
) -> None:
    if operation in {"RESERVE", "SPEND"}:
        if row["locked_until"] and datetime.fromisoformat(row["locked_until"]) > now:
            raise BudgetPersistenceError("budget is in cooldown")
        if (
            amount
            > balances["allocated"] + balances["won"] - balances["spent"] - balances["reserved"]
        ):
            raise BudgetPersistenceError("insufficient available budget")
    if operation in {"RESERVE", "SPEND", "SETTLE"} and amount > Decimal(
        row["limits"]["max_single_operation"]
    ):
        raise BudgetPersistenceError("single-operation limit exceeded")
    # Same exposure rules as BudgetController._check_limits: reservations count
    # as committed loss, so the persisted ledger never admits what memory denies.
    if operation in {"RESERVE", "SPEND"}:
        if balances["spent"] + balances["reserved"] + amount > Decimal(
            row["limits"]["max_total_loss"]
        ):
            raise BudgetPersistenceError("total-loss limit exceeded")
        if balances["loss_today"] + balances["reserved"] + amount > Decimal(
            row["limits"]["max_daily_loss"]
        ):
            raise BudgetPersistenceError("daily-loss limit exceeded")
    elif operation == "SETTLE":
        if balances["spent"] + amount > Decimal(row["limits"]["max_total_loss"]):
            raise BudgetPersistenceError("total-loss limit exceeded")
        if balances["loss_today"] + amount > Decimal(row["limits"]["max_daily_loss"]):
            raise BudgetPersistenceError("daily-loss limit exceeded")


def _mutate(
    row: dict[str, Any],
    balances: dict[str, Decimal],
    operation: str,
    amount: Decimal,
    now: datetime,
) -> None:
    _admit(row, balances, operation, amount, now)
    if operation in {"ALLOCATE", "WIN", "RESERVE"}:
        field = {"ALLOCATE": "allocated", "WIN": "won", "RESERVE": "reserved"}[operation]
        balances[field] += amount
    elif operation in {"RELEASE", "SETTLE"}:
        if amount > balances["reserved"]:
            raise BudgetPersistenceError("cannot consume more than reserved")
        balances["reserved"] -= amount
        if operation == "SETTLE":
            balances["spent"] += amount
            balances["loss_today"] += amount
    elif operation == "SPEND":
        balances["spent"] += amount
        balances["loss_today"] += amount
    elif operation == "REFUND":
        if amount > balances["spent"]:
            raise BudgetPersistenceError("cannot refund more than spent")
        # Earlier-period spend is returned first and never frees today's loss headroom.
        from_carried = min(amount, balances["carried_spent"])
        balances["carried_spent"] -= from_carried
        balances["spent"] -= amount
        balances["loss_today"] = max(Decimal("0"), balances["loss_today"] - (amount - from_carried))
    else:
        raise BudgetPersistenceError("unsupported budget operation")
