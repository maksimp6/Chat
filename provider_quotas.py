"""Per-user provider usage quotas and rate limiting.

Quotas are enforced before outbound provider requests and usage is recorded
against the trusted user identity associated with the current invocation.
SQLite uses an immediate transaction; PostgreSQL locks the usage row so two
concurrent requests cannot spend the same quota bucket.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Optional

from db import get_conn
from db_backend import is_postgres_configured


DEFAULT_POLICY = "default"
DEFAULT_PERIOD_SECONDS = 86400
DEFAULT_MAX_REQUESTS = 100
DEFAULT_MAX_REQUESTS_PER_MINUTE = 20


class ProviderQuotaExceeded(RuntimeError):
    """Raised when a trusted user's provider request cannot be admitted."""

    code = "provider_quota_exceeded"

    def __init__(
        self,
        *,
        reason: str,
        user_id: str,
        period_start: int,
        period_reset_at: int,
        used_requests: int,
        max_requests: int,
        rate_window_start: int,
        rate_reset_at: int,
        used_rate_requests: int,
        max_rate_requests: int,
    ) -> None:
        self.reason = reason
        self.user_id = user_id
        self.period_start = period_start
        self.period_reset_at = period_reset_at
        self.used_requests = used_requests
        self.max_requests = max_requests
        self.rate_window_start = rate_window_start
        self.rate_reset_at = rate_reset_at
        self.used_rate_requests = used_rate_requests
        self.max_rate_requests = max_rate_requests
        super().__init__(reason)

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code,
                "reason": self.reason,
                "user_id": self.user_id,
                "period": {
                    "used_requests": self.used_requests,
                    "max_requests": self.max_requests,
                    "reset_at": self.period_reset_at,
                },
                "rate_limit": {
                    "used_requests": self.used_rate_requests,
                    "max_requests": self.max_rate_requests,
                    "reset_at": self.rate_reset_at,
                },
            }
        }


@dataclass(frozen=True)
class QuotaReservation:
    user_id: str
    policy_name: str
    period_start: int
    period_reset_at: int
    reserved_request_number: int


def _now() -> int:
    return int(time.time())


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return max(minimum, value)


def _begin_transaction(conn) -> None:
    conn.execute("BEGIN IMMEDIATE" if not is_postgres_configured() else "BEGIN")


def _default_policy_values() -> tuple[int, int, int]:
    return (
        _env_int("ALICE_QUOTA_PERIOD_SECONDS", DEFAULT_PERIOD_SECONDS, 60),
        _env_int("ALICE_QUOTA_MAX_REQUESTS", DEFAULT_MAX_REQUESTS),
        _env_int(
            "ALICE_QUOTA_MAX_REQUESTS_PER_MINUTE",
            DEFAULT_MAX_REQUESTS_PER_MINUTE,
        ),
    )


def init_quota_tables() -> None:
    conn = get_conn()
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS provider_quota_policies (
                name TEXT PRIMARY KEY,
                period_seconds INTEGER NOT NULL,
                max_requests INTEGER NOT NULL,
                max_requests_per_minute INTEGER NOT NULL DEFAULT 0,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS provider_user_quota_plans (
                user_id TEXT PRIMARY KEY,
                policy_name TEXT NOT NULL,
                updated_at INTEGER NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS provider_quota_usage (
                user_id TEXT PRIMARY KEY,
                period_start INTEGER NOT NULL,
                request_count INTEGER NOT NULL DEFAULT 0,
                token_count INTEGER NOT NULL DEFAULT 0,
                cost REAL NOT NULL DEFAULT 0.0,
                rate_window_start INTEGER NOT NULL,
                rate_count INTEGER NOT NULL DEFAULT 0,
                updated_at INTEGER NOT NULL
            )"""
        )
        now = _now()
        period, max_requests, max_rate = _default_policy_values()
        conn.execute(
            """INSERT INTO provider_quota_policies
               (name, period_seconds, max_requests, max_requests_per_minute, enabled, created_at, updated_at)
               VALUES (?, ?, ?, ?, 1, ?, ?)
               ON CONFLICT(name) DO UPDATE SET
                 period_seconds=excluded.period_seconds,
                 max_requests=excluded.max_requests,
                 max_requests_per_minute=excluded.max_requests_per_minute,
                 enabled=excluded.enabled,
                 updated_at=excluded.updated_at""",
            (DEFAULT_POLICY, period, max_requests, max_rate, now, now),
        )
        conn.commit()
    finally:
        conn.close()


def upsert_policy(
    name: str,
    *,
    period_seconds: int,
    max_requests: int,
    max_requests_per_minute: int = 0,
    enabled: bool = True,
) -> None:
    name = str(name or "").strip().lower()
    if not name:
        raise ValueError("policy name is required")
    if period_seconds < 60:
        raise ValueError("period_seconds must be at least 60")
    if max_requests < 0 or max_requests_per_minute < 0:
        raise ValueError("quota limits cannot be negative")

    init_quota_tables()
    now = _now()
    conn = get_conn()
    try:
        conn.execute(
            """INSERT INTO provider_quota_policies
               (name, period_seconds, max_requests, max_requests_per_minute, enabled, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(name) DO UPDATE SET
                 period_seconds=excluded.period_seconds,
                 max_requests=excluded.max_requests,
                 max_requests_per_minute=excluded.max_requests_per_minute,
                 enabled=excluded.enabled,
                 updated_at=excluded.updated_at""",
            (
                name,
                int(period_seconds),
                int(max_requests),
                int(max_requests_per_minute),
                1 if enabled else 0,
                now,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def assign_user_policy(user_id: str, policy_name: str) -> None:
    user_id = str(user_id or "").strip()
    policy_name = str(policy_name or "").strip().lower()
    if not user_id:
        raise ValueError("user_id is required")
    if not policy_name:
        raise ValueError("policy_name is required")

    init_quota_tables()
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT 1 FROM provider_quota_policies WHERE name = ?",
            (policy_name,),
        ).fetchone()
        if not row:
            raise ValueError("quota policy not found")
        conn.execute(
            """INSERT INTO provider_user_quota_plans (user_id, policy_name, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                 policy_name=excluded.policy_name,
                 updated_at=excluded.updated_at""",
            (user_id, policy_name, _now()),
        )
        conn.commit()
    finally:
        conn.close()


def get_user_policy(user_id: str) -> dict[str, Any]:
    init_quota_tables()
    conn = get_conn()
    try:
        row = conn.execute(
            """SELECT p.*
               FROM provider_quota_policies p
               JOIN provider_user_quota_plans u
                 ON u.policy_name = p.name
              WHERE u.user_id = ?""",
            (user_id,),
        ).fetchone()
        if row is None:
            row = conn.execute(
                "SELECT * FROM provider_quota_policies WHERE name = ?",
                (DEFAULT_POLICY,),
            ).fetchone()
    finally:
        conn.close()

    if not row:
        raise RuntimeError("default quota policy is unavailable")
    return {
        "name": row["name"],
        "period_seconds": int(row["period_seconds"]),
        "max_requests": int(row["max_requests"]),
        "max_requests_per_minute": int(row["max_requests_per_minute"]),
        "enabled": bool(row["enabled"]),
    }


def reserve_request(
    user_id: Optional[str], *, now: Optional[int] = None
) -> Optional[QuotaReservation]:
    """Atomically reserve one provider request for a trusted user.

    A missing identity preserves backwards compatibility. Set
    ALICE_QUOTA_REQUIRE_IDENTITY=true to fail closed instead.
    """
    trusted_user_id = str(user_id or "").strip()
    if not trusted_user_id:
        if os.getenv("ALICE_QUOTA_REQUIRE_IDENTITY", "").strip().lower() in {"1", "true", "yes"}:
            raise ProviderQuotaExceeded(
                reason="trusted_user_identity_required",
                user_id="unknown",
                period_start=0,
                period_reset_at=0,
                used_requests=0,
                max_requests=0,
                rate_window_start=0,
                rate_reset_at=0,
                used_rate_requests=0,
                max_rate_requests=0,
            )
        return None

    now_value = int(now if now is not None else _now())
    policy = get_user_policy(trusted_user_id)
    if not policy["enabled"]:
        return None

    period_seconds = policy["period_seconds"]
    period_start = (now_value // period_seconds) * period_seconds
    period_reset_at = period_start + period_seconds
    rate_window_start = (now_value // 60) * 60
    rate_reset_at = rate_window_start + 60

    conn = get_conn()
    try:
        _begin_transaction(conn)
        select_sql = (
            "SELECT * FROM provider_quota_usage WHERE user_id = ? FOR UPDATE"
            if is_postgres_configured()
            else "SELECT * FROM provider_quota_usage WHERE user_id = ?"
        )
        usage = conn.execute(select_sql, (trusted_user_id,)).fetchone()
        if usage is None:
            conn.execute(
                """INSERT INTO provider_quota_usage
                   (user_id, period_start, request_count, token_count, cost,
                    rate_window_start, rate_count, updated_at)
                   VALUES (?, ?, 0, 0, 0, ?, 0, ?)
                   ON CONFLICT(user_id) DO NOTHING""",
                (trusted_user_id, period_start, rate_window_start, now_value),
            )
            usage = conn.execute(select_sql, (trusted_user_id,)).fetchone()

        current_period = int(usage["period_start"]) == period_start
        current_rate_window = int(usage["rate_window_start"]) == rate_window_start
        used_requests = int(usage["request_count"]) if current_period else 0
        used_rate_requests = int(usage["rate_count"]) if current_rate_window else 0

        max_requests = policy["max_requests"]
        max_rate = policy["max_requests_per_minute"]
        if max_requests > 0 and used_requests >= max_requests:
            raise ProviderQuotaExceeded(
                reason="period_quota_exhausted",
                user_id=trusted_user_id,
                period_start=period_start,
                period_reset_at=period_reset_at,
                used_requests=used_requests,
                max_requests=max_requests,
                rate_window_start=rate_window_start,
                rate_reset_at=rate_reset_at,
                used_rate_requests=used_rate_requests,
                max_rate_requests=max_rate,
            )
        if max_rate > 0 and used_rate_requests >= max_rate:
            raise ProviderQuotaExceeded(
                reason="rate_limit_exhausted",
                user_id=trusted_user_id,
                period_start=period_start,
                period_reset_at=period_reset_at,
                used_requests=used_requests,
                max_requests=max_requests,
                rate_window_start=rate_window_start,
                rate_reset_at=rate_reset_at,
                used_rate_requests=used_rate_requests,
                max_rate_requests=max_rate,
            )

        new_requests = used_requests + 1
        new_rate_count = used_rate_requests + 1
        conn.execute(
            """UPDATE provider_quota_usage
               SET period_start = ?,
                   request_count = ?,
                   rate_window_start = ?,
                   rate_count = ?,
                   updated_at = ?
             WHERE user_id = ?""",
            (
                period_start,
                new_requests,
                rate_window_start,
                new_rate_count,
                now_value,
                trusted_user_id,
            ),
        )
        conn.commit()
        return QuotaReservation(
            user_id=trusted_user_id,
            policy_name=policy["name"],
            period_start=period_start,
            period_reset_at=period_reset_at,
            reserved_request_number=new_requests,
        )
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def record_usage(
    reservation: Optional[QuotaReservation],
    *,
    token_count: int = 0,
    cost: float = 0.0,
) -> None:
    """Record actual provider usage against the reservation's period."""
    if reservation is None:
        return

    tokens = max(0, int(token_count or 0))
    numeric_cost = float(cost or 0.0)
    if numeric_cost < 0:
        raise ValueError("cost cannot be negative")

    conn = get_conn()
    try:
        _begin_transaction(conn)
        conn.execute(
            """UPDATE provider_quota_usage
               SET token_count = token_count + ?,
                   cost = cost + ?,
                   updated_at = ?
             WHERE user_id = ? AND period_start = ?""",
            (
                tokens,
                numeric_cost,
                _now(),
                reservation.user_id,
                reservation.period_start,
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_usage(user_id: str, *, now: Optional[int] = None) -> dict[str, Any]:
    user_id = str(user_id or "").strip()
    if not user_id:
        raise ValueError("user_id is required")

    policy = get_user_policy(user_id)
    now_value = int(now if now is not None else _now())
    period_start = (now_value // policy["period_seconds"]) * policy["period_seconds"]
    rate_window_start = (now_value // 60) * 60

    init_quota_tables()
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM provider_quota_usage WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        used_requests = used_tokens = 0
        used_rate = 0
    else:
        used_requests = int(row["request_count"]) if int(row["period_start"]) == period_start else 0
        used_tokens = int(row["token_count"]) if int(row["period_start"]) == period_start else 0
        used_rate = (
            int(row["rate_count"]) if int(row["rate_window_start"]) == rate_window_start else 0
        )

    return {
        "user_id": user_id,
        "policy": policy,
        "usage": {
            "requests": used_requests,
            "tokens": used_tokens,
            "cost": float(row["cost"]) if row and int(row["period_start"]) == period_start else 0.0,
            "rate_requests": used_rate,
        },
        "period_reset_at": period_start + policy["period_seconds"],
        "rate_reset_at": rate_window_start + 60,
    }


__all__ = [
    "ProviderQuotaExceeded",
    "QuotaReservation",
    "assign_user_policy",
    "get_usage",
    "get_user_policy",
    "init_quota_tables",
    "record_usage",
    "reserve_request",
    "upsert_policy",
]
