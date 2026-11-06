"""Provider request quotas, retired: nothing is counted and no request is refused.

The chat routes and the Yandex client still make these calls around each provider
request.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


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


def reserve_request(user_id: str | None) -> QuotaReservation | None:
    return None


def release_request(reservation: QuotaReservation | None) -> None:
    return None


def record_usage(reservation: QuotaReservation | None, *, token_count: int = 0) -> None:
    return None


__all__ = [
    "ProviderQuotaExceeded",
    "QuotaReservation",
    "record_usage",
    "release_request",
    "reserve_request",
]
