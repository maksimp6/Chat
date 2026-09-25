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
    ) -> Dict[str, Any]:
        amount = Decimal(str(amount))
        if amount < 0:
            raise ValueError("amount must be non-negative")

        conn = get_conn()
        try:
            cur = conn.cursor()
            # PostgreSQL exposes the deterministic transaction as an RPC-like
            # function. SQLite/local development must not silently emulate
            # financial concurrency semantics.
            if not hasattr(conn, "autocommit"):
                raise BudgetPersistenceError(
                    "budget persistence requires the PostgreSQL backend"
                )

            cur.execute(
                """
                SELECT apply_budget_operation(
                    %s, %s, %s, %s, %s, %s
                )
                """,
                (
                    budget_id,
                    account_type,
                    operation_type,
                    amount,
                    idempotency_key,
                    actor,
                ),
            )
            row = cur.fetchone()
            conn.commit()
            if not row:
                raise BudgetPersistenceError("budget operation returned no result")
            value = row[0]
            return json.loads(value) if isinstance(value, str) else value
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
