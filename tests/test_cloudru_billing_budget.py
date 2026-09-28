from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

import cloud.tools as cloud_tools
from budget_controller import (
    BudgetLimitExceeded,
    CloudBudgetGuard,
    CloudBudgetLimits,
    InvalidOperation,
    evaluate_cloud_spend,
)
from cloud.base import CloudProviderError
from cloud.cloudru.billing import parse_consumption_total
from cloud.cloudru.provider import CloudRuProvider
from cloud.policy import READ_ONLY_TOOLS
from cloud.tools import CLOUD_TOOLS, cloud_budget_status, cloud_compute


# --- billing payload parsing -------------------------------------------------


@pytest.mark.parametrize(
    "payload, total, currency, rows",
    [
        ({"total_cost": "1234.50", "currency": "rub"}, Decimal("1234.50"), "RUB", 0),
        ({"total": {"value": "10,5"}}, Decimal("10.5"), None, 0),
        ({"summary": {"total": 12}, "currency": "RUB"}, Decimal("12"), "RUB", 0),
        (
            {
                "items": [
                    {"cost": "100.10", "currency_code": "rub"},
                    {"amount": 50},
                    {"cost": "n/a", "price": {"amount": "0.40"}},
                    "garbage",
                    {"service": "no cost"},
                ]
            },
            Decimal("150.50"),
            "RUB",
            3,
        ),
        ({"data": [{"cost": True}], "rows": [{"sum": "7"}]}, Decimal("7"), None, 1),
        ({"summary": {"note": "empty"}, "items": []}, None, None, 0),
        ({"total": {"currency": "RUB"}, "total_cost": "NaN"}, None, None, 0),
        ([1, 2], None, None, 0),
    ],
)
def test_parse_consumption_total_shapes(payload, total, currency, rows):
    result = parse_consumption_total(payload)
    assert result == {"total_cost": total, "currency": currency, "rows": rows}


# --- provider costs_summary --------------------------------------------------


class RecordingClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def request(self, service, method, path, **kwargs):
        self.calls.append((service, method, path, kwargs))
        return self.payload


def _enable_billing(monkeypatch):
    monkeypatch.setenv("CLOUDRU_BILLING_ENDPOINT", "https://organization.api.cloud.ru")
    monkeypatch.setenv("CLOUDRU_BILLING_SUMMARY_PATH", "/v1/consumption")


def test_costs_summary_reports_total_and_currency(monkeypatch):
    _enable_billing(monkeypatch)
    client = RecordingClient({"items": [{"cost": "40"}, {"cost": "2.5"}]})
    result = CloudRuProvider(client=client).costs_summary(period="2026-09")
    assert result["total_cost"] == "42.5"
    assert result["currency"] == "RUB"
    assert client.calls == [
        ("billing", "GET", "/v1/consumption", {"params": {"period": "2026-09"}})
    ]


def test_costs_summary_unparseable_total_is_none(monkeypatch):
    _enable_billing(monkeypatch)
    monkeypatch.setenv("CLOUDRU_BILLING_CURRENCY", "usd")
    result = CloudRuProvider(client=RecordingClient({"summary": {"x": 1}})).costs_summary()
    assert result["total_cost"] is None
    assert result["currency"] == "USD"
    assert result["summary"] == {"x": 1}


# --- limits and evaluation ---------------------------------------------------


def test_limits_from_env():
    limits = CloudBudgetLimits.from_env(
        {
            "CLOUDRU_MONTHLY_BUDGET": "3000",
            "CLOUDRU_BUDGET_WARN_RATIO": "0.5",
            "CLOUDRU_BILLING_CURRENCY": "rub",
        }
    )
    assert limits == CloudBudgetLimits(Decimal("3000.00"), Decimal("0.5"), "RUB")
    assert CloudBudgetLimits.from_env({}).monthly_limit is None


def test_limits_from_process_env(monkeypatch):
    monkeypatch.setenv("CLOUDRU_MONTHLY_BUDGET", "10")
    assert CloudBudgetLimits.from_env().monthly_limit == Decimal("10.00")


@pytest.mark.parametrize("ratio", ["0", "1.5"])
def test_limits_reject_bad_warn_ratio(ratio):
    with pytest.raises(InvalidOperation):
        CloudBudgetLimits(Decimal("10"), Decimal(ratio))


@pytest.mark.parametrize(
    "spent, status, remaining",
    [
        ("100", "ok", "900.00"),
        ("800", "warn", "200.00"),
        ("1000", "block", "0.00"),
        ("1200", "block", "0.00"),
    ],
)
def test_evaluate_cloud_spend(spent, status, remaining):
    result = evaluate_cloud_spend(spent, CloudBudgetLimits(Decimal("1000")))
    assert result["status"] == status
    assert result["remaining"] == remaining
    assert result["currency"] == "RUB"


def test_evaluate_cloud_spend_edge_cases():
    assert evaluate_cloud_spend("5", CloudBudgetLimits(None)) == {
        "status": "unconfigured",
        "currency": "RUB",
    }
    assert evaluate_cloud_spend(None, CloudBudgetLimits(Decimal("5")))["status"] == "unknown"
    zero = evaluate_cloud_spend("0", CloudBudgetLimits(Decimal("0")))
    assert zero["status"] == "block"
    assert zero["ratio"] is None


# --- guard -------------------------------------------------------------------


class FakeBillingProvider:
    name = "fake-billing"

    def __init__(self, total="0", currency="RUB", error=None):
        self.total = total
        self.currency = currency
        self.error = error
        self.calls = 0
        self.compute_calls = []

    def costs_summary(self, *, period=None, group_by=None):
        self.calls += 1
        if self.error:
            raise self.error
        return {"provider": self.name, "total_cost": self.total, "currency": self.currency}

    def compute(self, **kwargs):
        self.compute_calls.append(kwargs)
        return {"ok": True, **kwargs}


def test_guard_caches_and_refreshes():
    now = [datetime(2026, 9, 28, tzinfo=timezone.utc)]
    provider = FakeBillingProvider(total="100")
    guard = CloudBudgetGuard(
        provider, CloudBudgetLimits(Decimal("1000")), cache_seconds=60, clock=lambda: now[0]
    )
    assert guard.status()["status"] == "ok"
    provider.total = "950"
    assert guard.status()["status"] == "ok"
    assert provider.calls == 1
    now[0] += timedelta(seconds=61)
    assert guard.status()["status"] == "warn"
    assert guard.status(refresh=True)["status"] == "warn"
    assert provider.calls == 3


def test_guard_blocks_and_traces_when_over_limit():
    events = []
    guard = CloudBudgetGuard(
        FakeBillingProvider(total="1500"),
        CloudBudgetLimits(Decimal("1000")),
        trace_sink=events.append,
    )
    with pytest.raises(BudgetLimitExceeded, match="1500.00 of 1000.00 RUB"):
        guard.enforce("cloud.compute.start")
    assert events[0]["event"] == "cloud_budget_block"


def test_guard_allows_when_unconfigured_or_billing_down():
    unconfigured = FakeBillingProvider(total="999999")
    guard = CloudBudgetGuard(unconfigured, CloudBudgetLimits(None))
    assert guard.enforce("op")["status"] == "unconfigured"
    assert unconfigured.calls == 0

    events = []
    down = CloudBudgetGuard(
        FakeBillingProvider(error=CloudProviderError("down", code="http_error")),
        CloudBudgetLimits(Decimal("10")),
        trace_sink=events.append,
    )
    result = down.enforce("op")
    assert result["status"] == "unknown"
    assert result["error"] == "CloudProviderError"
    assert events[0]["event"] == "cloud_budget_unknown"


def test_guard_treats_currency_mismatch_as_unknown():
    guard = CloudBudgetGuard(
        FakeBillingProvider(total="5000", currency="usd"), CloudBudgetLimits(Decimal("10"))
    )
    result = guard.enforce("op")
    assert result["status"] == "unknown"
    assert result["billing_currency"] == "USD"


# --- tools -------------------------------------------------------------------


@pytest.fixture
def fake_provider(monkeypatch):
    provider = FakeBillingProvider(total="100")
    monkeypatch.setattr(cloud_tools, "_provider", lambda args: provider)
    monkeypatch.setattr(cloud_tools, "_BUDGET_GUARDS", {})
    monkeypatch.setenv("CLOUDRU_MONTHLY_BUDGET", "1000")
    return provider


def test_budget_status_tool_is_read_only(fake_provider):
    assert "cloud.budget.status" in READ_ONLY_TOOLS
    assert CLOUD_TOOLS["cloud.budget.status"]["requires_approval"] is False
    result = cloud_budget_status({"provider": "fake-billing"})
    assert result["status"] == "ok"
    assert result["spent"] == "100.00"


def test_compute_start_blocked_over_budget(fake_provider):
    fake_provider.total = "1000"
    result = cloud_compute({"operation": "start", "instance_id": "vm-1"})
    assert result["success"] is False
    assert result["metadata"]["provider_code"] == "budget_exceeded"
    assert fake_provider.compute_calls == []


def test_compute_stop_is_never_budget_blocked(fake_provider):
    fake_provider.total = "1000"
    result = cloud_compute({"operation": "stop", "instance_id": "vm-1"})
    assert result["ok"] is True
    assert fake_provider.calls == 0


def test_compute_start_allowed_under_budget_and_guard_reused(fake_provider):
    assert cloud_compute({"operation": "start", "instance_id": "vm-1"})["ok"] is True
    assert cloud_compute({"operation": "start", "instance_id": "vm-2"})["ok"] is True
    assert fake_provider.calls == 1
