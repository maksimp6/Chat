"""Durable paper balance ledger; no real payment processing."""

from __future__ import annotations
from datetime import datetime, timezone
import math
import json
import threading
from typing import Any
from storage.db import get_store


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_treasury_tables() -> None:
    get_store()


def _empty_account(owner_id: str) -> dict[str, Any]:
    return {"owner_id": owner_id, "balance": 0.0, "currency": "RUB", "updated_at": _now()}


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
    return {**account, "ledger": sorted(ledger, key=lambda row: row["id"], reverse=True)}


def _record(
    owner_id: str, amount: Any, description: str, kind: str, reference: str | None = None
) -> bool:
    amount = float(amount)
    if not math.isfinite(amount) or amount <= 0 or not description or not owner_id:
        raise ValueError("owner, positive finite amount and description are required")
    store = get_store()
    account_key = "treasury/account/" + owner_id
    reference_key = "treasury/reference/" + json.dumps([owner_id, reference], ensure_ascii=False)
    with store.transaction() as tx:
        if reference and tx.get(reference_key) is not None:
            return False
        account = tx.get(account_key) or _empty_account(owner_id)
        if kind == "debit" and account["balance"] < amount:
            raise ValueError("insufficient balance")
        now = _now()
        identity = int(tx.get("treasury/next-ledger-id") or 0) + 1
        balance = account["balance"] + (amount if kind == "credit" else -amount)
        tx.set(account_key, {**account, "balance": balance, "updated_at": now})
        tx.set("treasury/next-ledger-id", identity)
        tx.set(
            "treasury/ledger/" + str(identity),
            {
                "id": identity,
                "owner_id": owner_id,
                "kind": kind,
                "amount": amount,
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
