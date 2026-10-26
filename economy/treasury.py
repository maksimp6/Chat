"""Durable paper balance ledger; no real payment processing."""

from __future__ import annotations
from decimal import Decimal, InvalidOperation
import json
import threading
from typing import Any
from core.clock import utc_iso
from storage.db import get_store


def _money(value: Any) -> Decimal:
    """Read an amount exactly. A float is taken by its shortest decimal text, so 0.1 stays 0.1 and
    ten top-ups of 0.1 add up to exactly 1, which float arithmetic does not give."""
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("amount must be a number") from exc
    if not result.is_finite():
        raise ValueError("amount must be a finite number")
    return result


def init_treasury_tables() -> None:
    get_store()


def _empty_account(owner_id: str) -> dict[str, Any]:
    return {"owner_id": owner_id, "balance": "0", "currency": "RUB", "updated_at": utc_iso()}


def get_account(owner_id: str = "default") -> dict[str, Any]:
    store = get_store()
    key = "treasury/account/" + owner_id
    with store.transaction() as tx:
        account = tx.get(key)
        if account is None:
            account = _empty_account(owner_id)
            tx.set(key, account)
        ledger = [
            row
            for row in store.items(prefix="treasury/ledger/").values()
            if row["owner_id"] == owner_id
        ]
    return {
        **account,
        "balance": float(_money(account["balance"])),
        "ledger": sorted(ledger, key=lambda row: row["id"], reverse=True),
    }


def _record(
    owner_id: str, amount: Any, description: str, kind: str, reference: str | None = None
) -> bool:
    value = _money(amount)
    if value <= 0 or not description or not owner_id:
        raise ValueError("owner, positive finite amount and description are required")
    store = get_store()
    account_key = "treasury/account/" + owner_id
    reference_key = "treasury/reference/" + json.dumps([owner_id, reference], ensure_ascii=False)
    with store.transaction() as tx:
        if reference and tx.get(reference_key) is not None:
            return False
        account = tx.get(account_key) or _empty_account(owner_id)
        current = _money(account["balance"])
        if kind == "debit" and current < value:
            raise ValueError("insufficient balance")
        now = utc_iso()
        identity = int(tx.get("treasury/next-ledger-id") or 0) + 1
        balance = current + value if kind == "credit" else current - value
        tx.set(account_key, {**account, "balance": str(balance), "updated_at": now})
        tx.set("treasury/next-ledger-id", identity)
        tx.set(
            "treasury/ledger/" + str(identity),
            {
                "id": identity,
                "owner_id": owner_id,
                "kind": kind,
                "amount": float(value),
                "description": description,
                "reference": reference,
                "created_at": now,
            },
        )
        if reference:
            tx.set(reference_key, identity)
    return True


# Largest demo credit in one call, and the most one owner may receive in total. Without the
# total bound a caller could repeat the single-call limit without end.
DEMO_TOP_UP_LIMIT = 10_000.0
DEMO_TOP_UP_TOTAL_LIMIT = 10_000.0
_DEMO_LOCK = threading.Lock()


def demo_top_up(owner_id: str, amount: Any, description: str = "Demo top-up") -> dict[str, Any]:
    try:
        value = float(amount)
    except (TypeError, ValueError) as exc:
        raise ValueError("amount must be a number") from exc
    if value > DEMO_TOP_UP_LIMIT:
        raise ValueError(f"amount exceeds the demo top-up limit of {DEMO_TOP_UP_LIMIT:g}")
    key = "treasury/demo/" + str(owner_id)
    # The check, the credit and the running total are one step for this process.
    with _DEMO_LOCK:
        credited = float((get_store().get(key) or {}).get("total", 0.0))
        if credited + value > DEMO_TOP_UP_TOTAL_LIMIT:
            raise ValueError(
                f"demo top-ups are limited to {DEMO_TOP_UP_TOTAL_LIMIT:g} per owner"
            )
        _record(owner_id, value, description, "credit")
        with get_store().transaction() as tx:
            tx.set(key, {"total": credited + value})
    return get_account(owner_id)


def post_expense(owner_id: str, amount: Any, description: str, reference: str) -> bool:
    """Debit once per reference. Returns whether this call posted it, without reading the ledger."""
    return _record(owner_id, amount, description, "debit", reference)


def record_expense(
    owner_id: str,
    amount: Any,
    description: str,
    reference: str | None = None,
    *,
    return_status: bool = False,
) -> Any:
    created = _record(owner_id, amount, description, "debit", reference)
    account = get_account(owner_id)
    return (account, created) if return_status else account
