"""Tests verify that future-work stubs raise NotImplementedError."""

import pytest
from alice_platform.providers.secrets import get_secret, resolve_all_secrets
from alice_platform.providers.dns import create_dns_record, update_dns_records, verify_dns
from alice_platform.providers.storage import (
    create_storage,
    delete_storage,
    create_snapshot,
    restore_from_snapshot,
    cleanup_old_snapshots,
)


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
