"""Persistence adapter for the Issue #8 budget controller.

The domain controller remains the policy boundary. This adapter delegates
atomic mutations to the database transaction function when PostgreSQL is
available and is intentionally explicit about the persistence boundary.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any, Dict, Optional

from db import get_conn
from db_backend import is_postgres_configured

try:
    from trace_manager import get_current_trace
except Exception:  # pragma: no cover - trace integration is optional
    get_current_trace = None


class BudgetPersistenceError(RuntimeError):
    pass


class BudgetRepository:
    """PostgreSQL-backed budget operation gateway."""

    def apply(
        self,
        budget_id: str,
        account_type: str,
        operation_type: str,
        amount: Any,
        *,
        idempotency_key: Optional[str] = None,
        actor: Optional[str] = None,
        cooldown_seconds: int = 0,
    ) -> Dict[str, Any]:
        amount = Decimal(str(amount))
        if amount < 0:
            raise ValueError("amount must be non-negative")

        conn = get_conn()
        try:
            if not is_postgres_configured():
                raise BudgetPersistenceError("budget persistence requires the PostgreSQL backend")

            cur = conn.cursor()
            cur.execute(
                """
                SELECT apply_budget_operation(
                    ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    budget_id,
                    account_type,
                    operation_type,
                    amount,
                    idempotency_key,
                    actor,
                    cooldown_seconds,
                ),
            )
            row = cur.fetchone()
            conn.commit()
            if not row:
                raise BudgetPersistenceError("budget operation returned no result")

            value = json.loads(row[0]) if isinstance(row[0], str) else row[0]
            trace = get_current_trace() if get_current_trace is not None else None
            if trace is not None:
                trace.add_event(
                    "budget_operation_persisted",
                    {
                        "budget_id": budget_id,
                        "account_type": account_type,
                        "operation_type": operation_type,
                        "amount": str(amount),
                        "operation_id": value.get("operation_id")
                        if isinstance(value, dict)
                        else None,
                        "status": value.get("status") if isinstance(value, dict) else None,
                        "idempotency_key": idempotency_key,
                    },
                )
            return value
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
