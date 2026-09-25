import os
import tempfile
from concurrent.futures import ThreadPoolExecutor

import db

from provider_quotas import (
    ProviderQuotaExceeded,
    assign_user_policy,
    get_usage,
    init_quota_tables,
    record_usage,
    reserve_request,
    upsert_policy,
)


def _setup(tmp, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", os.path.join(tmp, "quota.db"))
    init_quota_tables()


def test_user_quotas_are_isolated_and_usage_is_recorded(monkeypatch):
    old = db.DB_PATH
    with tempfile.TemporaryDirectory() as tmp:
        try:
            _setup(tmp, monkeypatch)
            upsert_policy("small", period_seconds=3600, max_requests=5, max_requests_per_minute=0)
            assign_user_policy("user-a", "small")
            assign_user_policy("user-b", "small")

            first = reserve_request("user-a", now=3_600_001)
            assert first is not None
            record_usage(first, token_count=42, cost=0.125)

            usage_a = get_usage("user-a")
            usage_b = get_usage("user-b")
            assert usage_a["usage"]["requests"] == 1
            assert usage_a["usage"]["tokens"] == 42
            assert usage_a["usage"]["cost"] == 0.125
            assert usage_b["usage"]["requests"] == 0
        finally:
            db.DB_PATH = old


def test_quota_boundary_and_reset(monkeypatch):
    old = db.DB_PATH
    with tempfile.TemporaryDirectory() as tmp:
        try:
            _setup(tmp, monkeypatch)
            upsert_policy("tiny", period_seconds=60, max_requests=2, max_requests_per_minute=0)
            assign_user_policy("user-1", "tiny")

            reserve_request("user-1", now=120)
            reserve_request("user-1", now=121)
            try:
                reserve_request("user-1", now=122)
            except ProviderQuotaExceeded as exc:
                assert exc.reason == "period_quota_exhausted"
                assert exc.used_requests == 2
                assert exc.max_requests == 2
            else:
                raise AssertionError("quota exhaustion was not enforced")

            renewed = reserve_request("user-1", now=180)
            assert renewed is not None
            assert renewed.reserved_request_number == 1
        finally:
            db.DB_PATH = old


def test_per_minute_rate_limit(monkeypatch):
    old = db.DB_PATH
    with tempfile.TemporaryDirectory() as tmp:
        try:
            _setup(tmp, monkeypatch)
            upsert_policy("rate", period_seconds=3600, max_requests=10, max_requests_per_minute=2)
            assign_user_policy("user-rate", "rate")

            reserve_request("user-rate", now=121)
            reserve_request("user-rate", now=122)
            try:
                reserve_request("user-rate", now=123)
            except ProviderQuotaExceeded as exc:
                assert exc.reason == "rate_limit_exhausted"
                assert exc.used_rate_requests == 2
            else:
                raise AssertionError("rate limit was not enforced")
        finally:
            db.DB_PATH = old


def test_concurrent_requests_cannot_overspend_sqlite_quota(monkeypatch):
    old = db.DB_PATH
    with tempfile.TemporaryDirectory() as tmp:
        try:
            _setup(tmp, monkeypatch)
            upsert_policy("one", period_seconds=3600, max_requests=1, max_requests_per_minute=0)
            assign_user_policy("race-user", "one")

            def attempt():
                try:
                    reservation = reserve_request("race-user", now=1_801)
                    return ("ok", reservation.reserved_request_number)
                except ProviderQuotaExceeded:
                    return ("denied", None)

            with ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(lambda _: attempt(), range(8)))

            assert sum(kind == "ok" for kind, _ in results) == 1
            assert sum(kind == "denied" for kind, _ in results) == 7
            assert get_usage("race-user")["usage"]["requests"] == 1
        finally:
            db.DB_PATH = old
