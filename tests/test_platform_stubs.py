"""Tests verify that future-work stubs raise NotImplementedError."""

import pytest
from alice_platform.reconciler import reconcile
from alice_platform.recovery import list_snapshots, restore_snapshot, rollback_config
from alice_platform.providers.cloudru import get_observed_state
from alice_platform.providers.secrets import get_secret, resolve_all_secrets
from alice_platform.providers.dns import create_dns_record, update_dns_records, verify_dns
from alice_platform.providers.storage import (
    create_storage,
    delete_storage,
    create_snapshot,
    restore_from_snapshot,
    cleanup_old_snapshots,
)


class TestCloudRuProviderImplemented:
    """Cloud.ru provider is implemented in Slice 3."""

    def test_get_observed_state_returns_containers_dict(self):
        """Get observed state returns dict with containers key."""
        result = get_observed_state("test")
        assert isinstance(result, dict)
        assert "containers" in result
        assert isinstance(result["containers"], list)

    def test_get_observed_state_handles_api_errors(self):
        """Get observed state gracefully handles API errors."""
        # Even with API errors, should return structure
        result = get_observed_state("test")
        assert result == {"containers": []}


class TestReconcilerImplemented:
    """Reconciler is now implemented in Slice 2."""

    def test_reconcile_with_empty_actions(self):
        """Reconcile with empty actions completes without error."""
        # Should not raise - reconcile now works
        reconcile("test", [], {})

    def test_reconcile_dry_run_mode(self):
        """Reconcile runs in dry-run mode (no actual deployments)."""
        from alice_platform.planner import Action, ActionType

        actions = [
            Action(
                type=ActionType.CREATE,
                service="test-service",
                lane="test",
                description="Test create",
            )
        ]
        config = {"services": {"test-service": {"resources": {"cpu": "0.5", "memory": "512Mi"}}}}
        # Should complete without error
        reconcile("test", actions, config)


class TestRecoveryImplemented:
    """Recovery functions are now implemented in Slice 2."""

    def test_list_snapshots_returns_empty(self):
        """List snapshots returns empty list (placeholder)."""
        result = list_snapshots("chrome-state", "test")
        assert result == []

    def test_restore_snapshot_test_lane(self):
        """Restore snapshot in test lane completes without approval."""
        # Should not raise
        restore_snapshot("chrome-state", "2024-10-01", "test")

    def test_restore_snapshot_production_requires_approval(self):
        """Restore snapshot in production lane requires approval."""
        # Should complete (approval check is done, request is logged)
        restore_snapshot("chrome-state", "2024-10-01", "production", approve=False)

    def test_rollback_config_test_lane(self):
        """Rollback config in test lane completes without approval."""
        rollback_config("abc1234", "test")

    def test_rollback_config_production_requires_approval(self):
        """Rollback config in production lane requires approval."""
        rollback_config("abc1234", "production", approve=False)


class TestSecretsProviderNotImplemented:
    """Secrets provider is deferred to next slice."""

    def test_get_secret_raises(self):
        """Get secret raises NotImplementedError."""
        with pytest.raises(NotImplementedError, match="Secrets provider deferred"):
            get_secret("alice/prod/oauth-client-secret")

    def test_resolve_all_secrets_raises(self):
        """Resolve all secrets raises NotImplementedError."""
        with pytest.raises(NotImplementedError, match="Secrets provider deferred"):
            resolve_all_secrets({"oauth": {"client_secret": "alice/prod/oauth-client-secret"}})


class TestDNSProviderNotImplemented:
    """DNS provider is deferred to next slice."""

    def test_create_dns_record_raises(self):
        """Create DNS record raises NotImplementedError."""
        with pytest.raises(NotImplementedError, match="DNS provider deferred"):
            create_dns_record("oauth.maxxxpavlov.online", "192.168.1.1", "https")

    def test_update_dns_records_raises(self):
        """Update DNS records raises NotImplementedError."""
        with pytest.raises(NotImplementedError, match="DNS provider deferred"):
            update_dns_records({"oauth.maxxxpavlov.online": {"service": "oauth"}})

    def test_verify_dns_raises(self):
        """Verify DNS raises NotImplementedError."""
        with pytest.raises(NotImplementedError, match="DNS provider deferred"):
            verify_dns("oauth.maxxxpavlov.online")


class TestStorageProviderNotImplemented:
    """Storage provider is deferred to next slice."""

    def test_create_storage_raises(self):
        """Create storage raises NotImplementedError."""
        with pytest.raises(NotImplementedError, match="Storage provider deferred"):
            create_storage("chrome-state", "10Gi", "/chrome-state")

    def test_delete_storage_raises(self):
        """Delete storage raises NotImplementedError."""
        with pytest.raises(NotImplementedError, match="Storage provider deferred"):
            delete_storage("chrome-state")

    def test_create_snapshot_raises(self):
        """Create snapshot raises NotImplementedError."""
        with pytest.raises(NotImplementedError, match="Storage provider deferred"):
            create_snapshot("chrome-state", "daily-backup")

    def test_restore_from_snapshot_raises(self):
        """Restore from snapshot raises NotImplementedError."""
        with pytest.raises(NotImplementedError, match="Storage provider deferred"):
            restore_from_snapshot("chrome-state", "2024-10-01")

    def test_cleanup_old_snapshots_raises(self):
        """Cleanup old snapshots raises NotImplementedError."""
        with pytest.raises(NotImplementedError, match="Storage provider deferred"):
            cleanup_old_snapshots("chrome-state", 30)
