"""Per-user provider usage quotas and rate limiting.

Quotas are enforced before outbound provider requests and usage is recorded
against the trusted user identity associated with the current invocation.
The native Kristall write barrier serializes reservation and usage mutations.
"""

from __future__ import annotations
import os
import time
from provider_accounts._vendor.restrictions import restrictions_lifted

from dataclasses import dataclass
from typing import Any, Optional

from storage.db import get_store


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
    rate_window_start: int = 0


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default
    return max(minimum, value)


def _default_policy_values() -> tuple[int, int, int]:
    return (
        _env_int("LOGOS_QUOTA_PERIOD_SECONDS", DEFAULT_PERIOD_SECONDS, 60),
        _env_int("LOGOS_QUOTA_MAX_REQUESTS", DEFAULT_MAX_REQUESTS),
        _env_int(
            "LOGOS_QUOTA_MAX_REQUESTS_PER_MINUTE",
            DEFAULT_MAX_REQUESTS_PER_MINUTE,
        ),
    )


def init_quota_tables() -> None:
    period, maximum, rate = _default_policy_values()
    now = int(time.time())
    key = "quota/policy/" + DEFAULT_POLICY
    with get_store().transaction() as tx:
        old = tx.get(key)
        row = {
            "name": DEFAULT_POLICY,
            "period_seconds": period,
            "max_requests": maximum,
            "max_requests_per_minute": rate,
            "enabled": True,
            "created_at": old["created_at"] if old else now,
            "updated_at": now,
        }
        # The environment only seeds the default policy. Re-applying it on every request reverted
        # any administrator change the next time a quota was checked.
        if old is None:
            tx.set(key, row)


def upsert_policy(
    name: str,
    *,
    period_seconds: int,
    max_requests: int,
    max_requests_per_minute: int = 0,
    enabled: bool = True,
) -> None:
    name = str(name or "").strip().lower()
    if (
        not name
        or period_seconds < 60
        or max_requests < 0
        or max_requests_per_minute < 0
    ):
        raise ValueError("invalid quota policy")
    init_quota_tables()
    key = "quota/policy/" + name
    now = int(time.time())
    with get_store().transaction() as tx:
        old = tx.get(key)
        tx.set(
            key,
            {
                "name": name,
                "period_seconds": int(period_seconds),
                "max_requests": int(max_requests),
                "max_requests_per_minute": int(max_requests_per_minute),
                "enabled": bool(enabled),
                "created_at": old["created_at"] if old else now,
                "updated_at": now,
            },
        )


def assign_user_policy(user_id: str, policy_name: str) -> None:
    user_id, policy_name = (
        str(user_id or "").strip(),
        str(policy_name or "").strip().lower(),
    )
    if not user_id or not policy_name:
        raise ValueError("user and policy are required")
    init_quota_tables()
    with get_store().transaction() as tx:
        if tx.get("quota/policy/" + policy_name) is None:
            raise ValueError("quota policy not found")
        tx.set("quota/plan/" + user_id, policy_name)


def get_user_policy(user_id: str) -> dict[str, Any]:
    init_quota_tables()
    store = get_store()
    with store.transaction() as tx:
        return _policy_for(tx, user_id)


def release_request(reservation: Optional[QuotaReservation]) -> None:
    """Return a reservation whose provider call produced no answer.

    The counts are lowered only while the reserved period and rate window are still current, so a
    release never reduces a later period's usage.
    """
    if reservation is None:
        return
    init_quota_tables()
    key = "quota/usage/" + reservation.user_id
    with get_store().transaction() as tx:
        usage = tx.get(key)
        if usage is None:
            return
        if (
            int(usage["period_start"]) == reservation.period_start
            and int(usage["request_count"]) > 0
        ):
            usage["request_count"] = int(usage["request_count"]) - 1
        if (
            int(usage["rate_window_start"]) == reservation.rate_window_start
            and int(usage["rate_count"]) > 0
        ):
            usage["rate_count"] = int(usage["rate_count"]) - 1
        tx.set(key, usage)


def reserve_request(
    user_id: Optional[str], *, now: Optional[int] = None
) -> Optional[QuotaReservation]:
    """Atomically reserve one provider request for a trusted user.

    A missing identity fails closed by default: an uncounted provider call would
    bypass every quota. Only LOGOS_QUOTA_REQUIRE_IDENTITY=false (for headless local
    runs) lets anonymous requests through unmetered.
    """
    if restrictions_lifted():
        return None
    trusted_user_id = str(user_id or "").strip()
    if not trusted_user_id:
        if os.environ.get(
            "LOGOS_QUOTA_REQUIRE_IDENTITY", "true"
        ).strip().lower() not in {
            "0",
            "false",
            "no",
        }:
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

    now_value = int(now if now is not None else int(time.time()))
    policy = get_user_policy(trusted_user_id)
    if not policy["enabled"]:
        return None

    period_seconds = policy["period_seconds"]
    period_start = (now_value // period_seconds) * period_seconds
    period_reset_at = period_start + period_seconds
    rate_window_start = (now_value // 60) * 60
    rate_reset_at = rate_window_start + 60

    init_quota_tables()
    with get_store().transaction() as tx:
        policy = _policy_for(tx, trusted_user_id)
        if not policy["enabled"]:
            return None
        period_seconds = policy["period_seconds"]
        period_start = (now_value // period_seconds) * period_seconds
        period_reset_at = period_start + period_seconds
        key = "quota/usage/" + trusted_user_id
        usage = tx.get(key) or {
            "user_id": trusted_user_id,
            "period_start": period_start,
            "request_count": 0,
            "token_count": 0,
            "cost": 0.0,
            "rate_window_start": rate_window_start,
            "rate_count": 0,
        }
        current_period = int(usage["period_start"]) == period_start
        current_rate_window = int(usage["rate_window_start"]) == rate_window_start
        used_requests = int(usage["request_count"]) if current_period else 0
        used_rate_requests = int(usage["rate_count"]) if current_rate_window else 0
        max_requests, max_rate = (
            policy["max_requests"],
            policy["max_requests_per_minute"],
        )
        reason = (
            "period_quota_exhausted"
            if max_requests > 0 and used_requests >= max_requests
            else "rate_limit_exhausted"
            if max_rate > 0 and used_rate_requests >= max_rate
            else None
        )
        if reason:
            raise ProviderQuotaExceeded(
                reason=reason,
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
        tx.set(
            key,
            {
                **usage,
                "period_start": period_start,
                "request_count": used_requests + 1,
                "token_count": usage["token_count"] if current_period else 0,
                "cost": usage["cost"] if current_period else 0.0,
                "rate_window_start": rate_window_start,
                "rate_count": used_rate_requests + 1,
                "updated_at": now_value,
            },
        )
        return QuotaReservation(
            user_id=trusted_user_id,
            policy_name=policy["name"],
            period_start=period_start,
            period_reset_at=period_reset_at,
            reserved_request_number=used_requests + 1,
            rate_window_start=rate_window_start,
        )


def record_usage(
    reservation: Optional[QuotaReservation],
    *,
    token_count: int = 0,
    cost: float = 0.0,
) -> None:
    if reservation is None:
        return
    tokens = max(0, int(token_count or 0))
    numeric_cost = float(cost or 0.0)
    if numeric_cost < 0:
        raise ValueError("cost cannot be negative")
    key = "quota/usage/" + reservation.user_id
    with get_store().transaction() as tx:
        row = tx.get(key)
        if row is not None:
            # Usage goes to the current period even when the period rolled over after the reservation;
            # dropping it under-reports what the user has spent.
            tx.set(
                key,
                {
                    **row,
                    "token_count": row["token_count"] + tokens,
                    "cost": row["cost"] + numeric_cost,
                    "updated_at": int(time.time()),
                },
            )


def get_usage(user_id: str, *, now: Optional[int] = None) -> dict[str, Any]:
    user_id = str(user_id or "").strip()
    if not user_id:
        raise ValueError("user_id is required")

    policy = get_user_policy(user_id)
    now_value = int(now if now is not None else int(time.time()))
    period_start = (now_value // policy["period_seconds"]) * policy["period_seconds"]
    rate_window_start = (now_value // 60) * 60

    init_quota_tables()
    row = get_store().get("quota/usage/" + user_id)

    if row is None:
        used_requests = used_tokens = 0
        used_rate = 0
    else:
        used_requests = (
            int(row["request_count"]) if int(row["period_start"]) == period_start else 0
        )
        used_tokens = (
            int(row["token_count"]) if int(row["period_start"]) == period_start else 0
        )
        used_rate = (
            int(row["rate_count"])
            if int(row["rate_window_start"]) == rate_window_start
            else 0
        )

    return {
        "user_id": user_id,
        "policy": policy,
        "usage": {
            "requests": used_requests,
            "tokens": used_tokens,
            "cost": float(row["cost"])
            if row and int(row["period_start"]) == period_start
            else 0.0,
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


def _policy_for(tx: Any, user_id: str) -> dict[str, Any]:
    name = tx.get("quota/plan/" + user_id) or DEFAULT_POLICY
    row = tx.get("quota/policy/" + name)
    if row is None and name != DEFAULT_POLICY:
        # A plan that names a removed policy falls back to the default instead of failing requests.
        row = tx.get("quota/policy/" + DEFAULT_POLICY)
    if row is None:
        raise RuntimeError("quota policy is unavailable")
    return {
        key: row[key]
        for key in (
            "name",
            "period_seconds",
            "max_requests",
            "max_requests_per_minute",
            "enabled",
        )
    }
