"""Reconciler integration tests for Alice Cloud."""

from __future__ import annotations

from alice_platform.planner import Action, ActionType
import alice_platform.reconciler as reconciler


class FakeProvider:
    name = "alice"

    def __init__(self) -> None:
        self.calls = []

    def reconcile_container(self, **kwargs):
        self.calls.append(kwargs)
        return {"state": "running"}


class FakeRegistry:
    def __init__(self, provider):
        self.provider = provider

    def get(self, name):
        assert name == "alice"
        return self.provider


def test_reconcile_applies_nonproduction_action(monkeypatch):
    provider = FakeProvider()
    monkeypatch.setattr(reconciler, "ensure_default_providers", lambda: FakeRegistry(provider))

    action = Action(ActionType.CREATE, "api", "dev")
    reconciler.reconcile(
        "dev",
        [action],
        {
            "cloud": {"provider": "alice"},
            "services": {"api": {"image": "api@sha256:test", "scale": 1}},
        },
    )

    assert action.applied is True
    assert provider.calls == [
        {
            "operation": "create",
            "lane": "dev",
            "service": "api",
            "config": {"image": "api@sha256:test", "scale": 1},
        }
    ]


def test_reconcile_does_not_apply_unapproved_production_action(monkeypatch):
    provider = FakeProvider()
    monkeypatch.setattr(reconciler, "ensure_default_providers", lambda: FakeRegistry(provider))

    action = Action(ActionType.DELETE, "api", "production", needs_approval=True)
    reconciler.reconcile(
        "production",
        [action],
        {"cloud": {"provider": "alice"}, "services": {}},
    )

    assert action.applied is False
    assert provider.calls == []
