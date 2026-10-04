"""Tests for Alice Platform planner (desired vs observed state)."""

import pytest
from alice_platform.planner import (
    plan_actions,
    Action,
    ActionType,
)


class TestPlannerBasics:
    """Basic planner functionality."""

    def test_empty_observed_creates_all_services(self):
        """When nothing is deployed, plan creates all services."""
        desired = {
            "services": {"oauth": {"type": "oauth"}, "chrome": {"type": "chrome"}},
            "lanes": {"test": {"sign_in": "passphrase", "services": ["oauth", "chrome"]}},
        }
        observed = {"containers": []}

        actions = plan_actions("test", desired, observed)

        # Should create both services
        create_actions = [a for a in actions if a.type == ActionType.CREATE]
        assert len(create_actions) == 2

    def test_matching_state_no_actions(self):
        """When desired matches observed, no actions needed."""
        desired = {
            "services": {"oauth": {"type": "oauth", "scale": 1}},
            "lanes": {"test": {"sign_in": "passphrase", "services": ["oauth"]}},
        }
        observed = {
            "containers": [
                {"service": "oauth", "lane": "test", "scale": 1, "image": "oauth:latest"}
            ]
        }

        actions = plan_actions("test", desired, observed)

        # No changes needed
        assert len(actions) == 0

    def test_scale_change_triggers_update(self):
        """Change in scale triggers UPDATE action."""
        desired = {
            "services": {"oauth": {"type": "oauth", "scale": 3}},
            "lanes": {"test": {"sign_in": "passphrase", "services": ["oauth"]}},
        }
        observed = {
            "containers": [
                {"service": "oauth", "lane": "test", "scale": 1, "image": "oauth:latest"}
            ]
        }

        actions = plan_actions("test", desired, observed)

        # Should update scale
        update_actions = [a for a in actions if a.type == ActionType.UPDATE]
        assert len(update_actions) == 1

    def test_orphan_container_reported(self):
        """Container from previous deployment reported as orphan."""
        desired = {
            "services": {"oauth": {"type": "oauth"}},
            "lanes": {"test": {"sign_in": "passphrase", "services": ["oauth"]}},
        }
        observed = {
            "containers": [
                {"service": "oauth", "lane": "test", "scale": 1, "image": "oauth:latest"},
                {"service": "chrome", "lane": "test", "scale": 1, "image": "chrome:old"},
            ]
        }

        actions = plan_actions("test", desired, observed)

        # Should report orphan
        orphan_actions = [a for a in actions if a.type == ActionType.REPORT_ORPHAN]
        assert len(orphan_actions) == 1


class TestProductionGates:
    """Production lane requires approval."""

    def test_production_action_needs_approval(self):
        """Actions in production lane marked as needs_approval."""
        desired = {
            "services": {"oauth": {"type": "oauth"}},
            "lanes": {"production": {"sign_in": "github", "services": ["oauth"]}},
        }
        observed = {"containers": []}

        actions = plan_actions("production", desired, observed)

        # Production actions should need approval
        assert len(actions) > 0
        for action in actions:
            if action.type in [ActionType.CREATE, ActionType.UPDATE]:
                assert action.needs_approval == True

    def test_test_action_no_approval(self):
        """Test lane actions don't need approval."""
        desired = {
            "services": {"oauth": {"type": "oauth"}},
            "lanes": {"test": {"sign_in": "passphrase", "services": ["oauth"]}},
        }
        observed = {"containers": []}

        actions = plan_actions("test", desired, observed)

        # Test actions don't need approval
        for action in actions:
            if action.type in [ActionType.CREATE, ActionType.UPDATE]:
                assert action.needs_approval == False


class TestDryRun:
    """Dry-run mode doesn't apply changes."""

    def test_plan_is_dry_run_by_default(self):
        """Planner produces a dry-run by default."""
        desired = {
            "services": {"oauth": {"type": "oauth"}},
            "lanes": {"test": {"sign_in": "passphrase", "services": ["oauth"]}},
        }
        observed = {"containers": []}

        actions = plan_actions("test", desired, observed)

        # Actions generated but not applied
        assert len(actions) > 0
        for action in actions:
            assert action.applied == False
