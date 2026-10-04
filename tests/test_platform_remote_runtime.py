"""Focused tests for issue #787 remote runtime foundations."""

from types import SimpleNamespace

import pytest

from alice_platform.providers.cloudru import get_observed_state
from alice_platform.reconciler import SAFE_OPERATIONS, reconcile
from alice_platform.recovery import RecoveryPolicy, bounded_recovery


class FakeApps:
    def list(self):
        return [
            {
                "name": "rdc-demo",
                "status": "running",
                "template": {
                    "containers": [{
                        "image": "registry/repo@sha256:" + "a" * 64,
                        "resources": {"cpu": "1", "memory": "4096Mi"},
                        "env": [{"name": "ALICE_RDC_MODE", "value": "secret-never-exposed"}],
                    }],
                    "scaling": {"minInstanceCount": 1, "maxInstanceCount": 1},
                },
                "configuration": {"ingress": {"publiclyAccessible": True}},
            }
        ]


def test_cloudru_observed_state_is_redacted():
    state = get_observed_state(
        "production",
        env={"CLOUDRU_PROJECT_ID": "project"},
        client_factory=lambda project: FakeApps(),
    )
    row = state["containers"][0]
    assert row["name"] == "rdc-demo"
    assert row["status"] == "running"
    assert row["env_names"] == ["ALICE_RDC_MODE"]
    assert "secret-never-exposed" not in repr(state)


def test_cloudru_unconfigured_is_explicit():
    assert get_observed_state("test", env={}) == {
        "containers": [],
        "provider": "cloudru",
        "configured": False,
    }


def test_reconciler_allows_only_bounded_runtime_operations():
    assert SAFE_OPERATIONS == {"start", "restart", "redeploy_same_revision"}
    called = []
    action = SimpleNamespace(service="remote-desktop", operation="restart")
    result = reconcile(
        "test",
        [action],
        {},
        executors={"restart": lambda service, config: called.append(service)},
    )
    assert called == ["remote-desktop"]
    assert result[0].status == "applied"


def test_reconciler_requires_production_approval():
    action = SimpleNamespace(service="remote-desktop", operation="restart")
    with pytest.raises(PermissionError):
        reconcile(
            "production",
            [action],
            {},
            executors={"restart": lambda service, config: None},
        )


def test_reconciler_rejects_destructive_operation():
    action = SimpleNamespace(service="remote-desktop", operation="delete")
    with pytest.raises(ValueError, match="unsafe"):
        reconcile("test", [action], {}, executors={})


def test_bounded_recovery_stops_after_success():
    attempts = []
    assert bounded_recovery(
        lambda: attempts.append(1) or len(attempts) == 2,
        policy=RecoveryPolicy(max_attempts=3),
    ) == 2


def test_bounded_recovery_has_hard_ceiling():
    with pytest.raises(RuntimeError, match="exhausted"):
        bounded_recovery(lambda: False, policy=RecoveryPolicy(max_attempts=2))
