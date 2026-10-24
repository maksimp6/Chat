"""Daily quota primitives required by Haiku; limits below are synthetic fixtures.

The owner has not yet selected a production daily allowance or its unit.
These tests do not configure a live account or authorize paid requests.
"""

import pytest

from provider_quotas import (
    ProviderQuotaExceeded,
    assign_user_policy,
    get_usage,
    init_quota_tables,
    record_usage,
    reserve_request,
    upsert_policy,
)


DAY = 86400


@pytest.fixture
def daily_policy(monkeypatch, tmp_path):
    import db

    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "daily-quota.db"))
    init_quota_tables()
    upsert_policy("synthetic-daily", period_seconds=DAY, max_requests=2, max_requests_per_minute=0)
    assign_user_policy("owner", "synthetic-daily")


def test_daily_requests_block_at_limit_and_reset_at_utc_midnight(daily_policy):
    reserve_request("owner", now=DAY + 1)
    reserve_request("owner", now=2 * DAY - 1)
    with pytest.raises(ProviderQuotaExceeded) as denied:
        reserve_request("owner", now=2 * DAY - 1)
    assert denied.value.period_reset_at == 2 * DAY
    assert denied.value.used_requests == denied.value.max_requests == 2
    renewed = reserve_request("owner", now=2 * DAY)
    assert renewed.reserved_request_number == 1
    assert renewed.period_reset_at == 3 * DAY


def test_new_day_resets_tokens_and_cost_alongside_requests(daily_policy):
    yesterday = reserve_request("owner", now=2 * DAY - 1)
    record_usage(yesterday, token_count=500, cost=0.01)
    today = reserve_request("owner", now=2 * DAY)
    record_usage(today, token_count=20, cost=0.002)
    usage = get_usage("owner", now=2 * DAY)["usage"]
    assert usage["requests"] == 1
    assert usage["tokens"] == 20
    assert usage["cost"] == pytest.approx(0.002)


def test_late_yesterday_response_does_not_consume_today_allowance(daily_policy):
    yesterday = reserve_request("owner", now=2 * DAY - 1)
    reserve_request("owner", now=2 * DAY)
    record_usage(yesterday, token_count=500, cost=0.01)
    usage = get_usage("owner", now=2 * DAY)["usage"]
    assert usage["requests"] == 1
    assert usage["tokens"] == 0
    assert usage["cost"] == 0
