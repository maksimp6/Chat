"""Current-time helpers. Each returns the type its call sites already relied on."""

from __future__ import annotations

import time
from datetime import UTC, datetime


def unix_seconds() -> int:
    return int(time.time())


def unix_time() -> float:
    return time.time()


def utc_now() -> datetime:
    return datetime.now(UTC)


def utc_iso() -> str:
    return utc_now().isoformat()
