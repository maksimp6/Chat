"""RED acceptance contract for #954 canonical Alice runtime desired state.

This file intentionally fails on the protected-master baseline until #954's
desired-state implementation is applied. It does not assert live Cloud.ru
provider acceptance, deployment, owner connection, or billing evidence.
"""

from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from alice_platform.config import ConfigError, load_config


EXPECTED_RUNTIME = {
    "alice": {
        "resources": {"cpu": "1", "memory": "1024Mi"},
        "min_instances": 0,
        "max_instances": 1,
        "idle_timeout_seconds": 60,
    },
    "alice-lab": {
        "resources": {"cpu": "1", "memory": "1024Mi"},
        "min_instances": 0,
        "max_instances": 1,
        "idle_timeout_seconds": 30,
    },
    "alice-browser": {
        "resources": {"cpu": "2", "memory": "2048Mi"},
        "min_instances": 0,
        "max_instances": 1,
        "idle_timeout_seconds": 30,
    },
}


def _service(**overrides):
    service = {
        "type": "alice",
        "depends_on": ["oauth"],
        "scale": 0,
        "resources": {"cpu": "1", "memory": "1024Mi"},
        "min_instances": 0,
        "max_instances": 1,
        "idle_timeout_seconds": 300,
    }
    service.update(overrides)
    return service


def test_repository_declares_complete_canonical_runtime_desired_state():
    config = load_config(Path("config/alice"))
    services = config["services"]

    for name, expected in EXPECTED_RUNTIME.items():
        service = services[name]
        assert service["resources"] == expected["resources"]
        assert service["scale"] == 0
        assert service["min_instances"] == expected["min_instances"]
        assert service["max_instances"] == expected["max_instances"]
        assert service["idle_timeout_seconds"] == expected["idle_timeout_seconds"]
        assert "oauth" in service["depends_on"]


def test_production_and_lab_lanes_are_isolated_and_browser_is_shared():
    config = load_config(Path("config/alice"))
    production = set(config["lanes"]["production"]["services"])
    lab = set(config["lanes"]["test"]["services"])

    assert {"alice", "alice-browser"} <= production
    assert "alice-lab" not in production
    assert {"alice-lab", "alice-browser"} <= lab
    assert "alice" not in lab


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("min_instances", -1),
        ("max_instances", 0),
        ("idle_timeout_seconds", 0),
        ("idle_timeout_seconds", "120"),
    ],
)
def test_scaling_contract_fails_closed(tmp_path, field, value):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    service = _service(**{field: value})
    (config_dir / "platform.yaml").write_text(
        yaml.safe_dump({"services": {"alice": service}}),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_config(config_dir)


def test_min_instances_cannot_exceed_max_instances(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "platform.yaml").write_text(
        """services:
  alice:
    type: alice
    depends_on: []
    scale: 0
    min_instances: 2
    max_instances: 1
    idle_timeout_seconds: 300
    resources:
      cpu: "1"
      memory: 1024Mi
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_config(config_dir)


def test_gross_runtime_cost_estimator_contract_exists_and_is_not_provider_billing():
    from cloud.cloudru import billing

    estimator = billing.estimate_container_runtime_cost
    result = estimator(
        active_seconds=3600,
        idle_seconds=300,
        cold_starts=1,
        vcpu=Decimal("1"),
        memory_gb=Decimal("1"),
        vcpu_rub_per_hour=Decimal("1.891"),
        memory_rub_per_gb_hour=Decimal("1.256966"),
    )

    assert result["billable_seconds"] == 3900
    assert result["estimated_rub"] == Decimal("3.4102965")
    assert result["free_tier_applied"] is False
    assert result["status"] == "estimated"


def test_desired_state_does_not_claim_live_provider_or_owner_acceptance():
    config = load_config(Path("config/alice"))
    serialized = repr(config).lower()
    for forbidden in (
        "cloudru_verified",
        "owner_connected",
        "live_acceptance_passed",
        "provider_billing_verified",
    ):
        assert forbidden not in serialized
