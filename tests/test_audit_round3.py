"""Regression tests for the third audit batch: Cloud.ru resource ids and live budget,
memory visibility defaults, ownership deletes, parser hardening and pricing windows."""

from decimal import Decimal

import pytest

from agent_memory.models import MemoryRecord
from cloud.base import CloudProviderError
from cloud.cloudru import provider as cloud_provider
from cloud.cloudru.live_budget import BudgetExceeded, LiveAcceptanceBudget
from economy.pricing_registry import PricingRegistry, PricingSnapshot
from yandex_client_modules.parsers import extract_usage


def test_live_budget_sums_actions_against_the_run_limit():
    budget = LiveAcceptanceBudget(max_rub=Decimal("10"), max_window_seconds=900)
    for _ in range(5):
        budget.authorize(predicted_rub=Decimal("2"), elapsed_seconds=1)
    assert budget.committed_rub == Decimal("10")
    with pytest.raises(BudgetExceeded):
        budget.authorize(predicted_rub=Decimal("2"), elapsed_seconds=1)


def test_live_budget_rejects_non_finite_or_negative_predictions():
    budget = LiveAcceptanceBudget(max_rub=Decimal("10"), max_window_seconds=900)
    with pytest.raises(ValueError):
        budget.authorize(predicted_rub=Decimal("NaN"), elapsed_seconds=1)
    with pytest.raises(ValueError):
        budget.authorize(predicted_rub=Decimal("-1"), elapsed_seconds=1)


def test_cleanup_reports_acceptance_only_for_a_successful_run():
    budget = LiveAcceptanceBudget(max_rub=Decimal("10"), max_window_seconds=900)
    budget.finish("success")
    assert budget.record_cleanup(success=True).acceptance_passed is True
    failed = LiveAcceptanceBudget(max_rub=Decimal("10"), max_window_seconds=900)
    failed.finish("failure")
    assert failed.record_cleanup(success=True).acceptance_passed is False


def test_resource_ids_with_path_characters_are_refused():
    with pytest.raises(CloudProviderError):
        cloud_provider._resource_id("../../other/endpoint")
    assert cloud_provider._resource_id("vm-1.prod_2") == "vm-1.prod_2"


def test_caller_extras_cannot_override_the_operation_or_instance():
    captured = {}

    class Client:
        def request(self, service, method, path, **kwargs):
            captured.update(path=path, body=kwargs.get("json_body"))
            return {}

    provider = cloud_provider.CloudRuProvider(client=Client())
    provider._service_path = lambda name: "/v1/instances"
    provider.compute(
        operation="start",
        instance_id="vm-1",
        extra={"operation": "delete", "instance_id": "vm-2"},
    )
    assert captured["body"]["operation"] == "start"
    assert captured["body"]["instance_id"] == "vm-1"


def test_default_memory_listing_hides_restricted_records():
    from agent_memory.repository import AgentMemoryStore

    class Db:
        def __init__(self, values):
            self.values = values

        def items(self, prefix):
            return self.values

    provenance = {"source_type": "conversation", "refs": ["conversation:1"]}
    open_record = MemoryRecord(
        memory_id="open",
        kind="project",
        scope="s",
        text="a",
        provenance=provenance,
        source_version="v",
        created_at=1,
        updated_at=1,
    )
    restricted = MemoryRecord(
        memory_id="restricted",
        kind="project",
        scope="s",
        text="b",
        provenance=provenance,
        source_version="v",
        visibility=("backend-engineer",),
        created_at=1,
        updated_at=1,
    )
    store = AgentMemoryStore(store=Db({"a": open_record.as_dict(), "b": restricted.as_dict()}))
    assert [r.memory_id for r in store.list_records()] == ["open"]
    assert {r.memory_id for r in store.list_records(include_restricted=True)} == {
        "open",
        "restricted",
    }


def test_ownership_delete_requires_an_owner():
    from conversations import ownership

    with pytest.raises(ValueError):
        ownership.delete_owner("conversation-a", "")


def test_usage_with_malformed_fields_yields_zero_counts():
    usage = extract_usage({"usage": {"input_tokens": "many", "output_tokens": float("nan")}})
    assert usage["input_tokens"] == 0
    assert usage["output_tokens"] == 0
    assert extract_usage({"usage": ["not", "a", "mapping"]})["total_tokens"] == 0


def test_overlapping_pricing_windows_are_refused():
    base = dict(
        pricing_version="v1",
        currency="RUB",
        source="s",
        source_updated="2026-01-01",
        provider="p",
        service="svc",
        operation="op",
        model="m",
    )
    registry = PricingRegistry()
    registry.register(PricingSnapshot(effective_from="2026-01-01", effective_to="2026-06-01", **base))
    with pytest.raises(ValueError, match="overlaps"):
        registry.register(
            PricingSnapshot(
                effective_from="2026-05-01",
                effective_to="2026-09-01",
                **{**base, "pricing_version": "v2"},
            )
        )
    registry.register(
        PricingSnapshot(
            effective_from="2026-06-01",
            effective_to=None,
            **{**base, "pricing_version": "v3"},
        )
    )


def test_negative_unit_price_is_refused():
    with pytest.raises(ValueError):
        PricingSnapshot(
            pricing_version="v1",
            effective_from="2026-01-01",
            effective_to=None,
            currency="RUB",
            source="s",
            source_updated="2026-01-01",
            provider="p",
            service="svc",
            operation="op",
            unit_price=-1.0,
        )
