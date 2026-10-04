"""Tests for Alice Platform config loading and validation."""

import json
import pytest
import yaml
from pathlib import Path

from alice_platform.config import (
    load_config,
    ConfigError,
)


def make_minimal_config(tmp_path, services=None, domains=None, lanes=None, secrets=None):
    """Helper to create a minimal valid config."""
    config_dir = tmp_path / "config"
    config_dir.mkdir(exist_ok=True)

    if services is None:
        services = {"oauth": {"type": "oauth"}}
    if lanes is None:
        lanes = {"test": {"sign_in": "passphrase", "services": list(services.keys())}}

    platform = {"services": services}
    (config_dir / "platform.yaml").write_text(yaml.dump(platform))

    if domains:
        (config_dir / "domains.yaml").write_text(yaml.dump(domains))

    if lanes:
        # Split lanes into files
        for lane_name, lane_config in lanes.items():
            lane_data = {"lanes": {lane_name: lane_config}}
            (config_dir / f"{lane_name}.yaml").write_text(yaml.dump(lane_data))

    if secrets:
        (config_dir / "secrets.yaml").write_text(yaml.dump(secrets))

    return config_dir


class TestValidConfigLoading:
    """Valid config should load without errors."""

    def test_valid_minimal_config(self):
        """Minimal valid config loads."""
        config_dir = Path(__file__).parent / "fixtures" / "valid_minimal"
        config = load_config(config_dir)
        assert config is not None
        assert "services" in config

    def test_valid_production_config(self):
        """Production config with all lanes loads."""
        config_dir = Path(__file__).parent / "fixtures" / "valid_production"
        config = load_config(config_dir)
        assert config is not None
        assert "lanes" in config
        assert "production" in config["lanes"]


class TestSchemaValidation:
    """Schema validation catches invalid structure."""

    def test_unknown_field_rejected(self, tmp_path):
        """Unknown fields cause schema validation error."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()

        platform = {"services": {"oauth": {"type": "oauth", "unknown_field": "value"}}}
        (config_dir / "platform.yaml").write_text(yaml.dump(platform))

        test_lane = {"lanes": {"test": {"sign_in": "passphrase", "services": ["oauth"]}}}
        (config_dir / "test.yaml").write_text(yaml.dump(test_lane))

        with pytest.raises(ConfigError, match="unknown_field"):
            load_config(config_dir)

    def test_missing_required_service_type(self, tmp_path):
        """Service without 'type' field fails validation."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()

        platform = {
            "services": {
                "oauth": {}  # Missing 'type'
            }
        }
        (config_dir / "platform.yaml").write_text(yaml.dump(platform))

        test_lane = {"lanes": {"test": {"sign_in": "passphrase", "services": ["oauth"]}}}
        (config_dir / "test.yaml").write_text(yaml.dump(test_lane))

        with pytest.raises(ConfigError, match="type"):
            load_config(config_dir)


class TestNoCycles:
    """Dependency cycles are rejected."""

    def test_self_dependency_rejected(self, tmp_path):
        """Service cannot depend on itself."""
        config_dir = make_minimal_config(
            tmp_path, services={"oauth": {"type": "oauth", "depends_on": ["oauth"]}}
        )

        with pytest.raises(ConfigError, match="cycle|dependency"):
            load_config(config_dir)

    def test_circular_dependency_rejected(self, tmp_path):
        """Circular dependencies between services rejected."""
        config_dir = make_minimal_config(
            tmp_path,
            services={
                "oauth": {"type": "oauth", "depends_on": ["chrome"]},
                "chrome": {"type": "chrome", "depends_on": ["oauth"]},
            },
        )

        with pytest.raises(ConfigError, match="cycle|dependency"):
            load_config(config_dir)

    def test_nonexistent_dependency_rejected(self, tmp_path):
        """Dependency on nonexistent service rejected."""
        config_dir = make_minimal_config(
            tmp_path, services={"oauth": {"type": "oauth", "depends_on": ["nonexistent"]}}
        )

        with pytest.raises(ConfigError, match="nonexistent|not found"):
            load_config(config_dir)


class TestPlaintextSecretsRejected:
    """Plaintext secrets in config are rejected."""

    def test_plaintext_secret_in_secrets_yaml(self, tmp_path):
        """Plaintext secret value in secrets.yaml rejected."""
        config_dir = make_minimal_config(
            tmp_path, secrets={"github_client_secret": "actual_secret_value_here"}
        )

        with pytest.raises(ConfigError, match="plaintext|secret"):
            load_config(config_dir)

    def test_base64_secret_rejected(self, tmp_path):
        """Base64-like values rejected as potential secrets."""
        config_dir = make_minimal_config(
            tmp_path,
            services={
                "oauth": {
                    "type": "oauth",
                    "client_id": "aGVsbG8gd29ybGQgd2l0aCBiYXNlNjQ=",  # base64
                }
            },
        )

        with pytest.raises(ConfigError, match="secret|plaintext"):
            load_config(config_dir)

    def test_pem_key_rejected(self, tmp_path):
        """PEM-formatted keys rejected."""
        config_dir = make_minimal_config(
            tmp_path,
            services={
                "oauth": {
                    "type": "oauth",
                    "signing_key": "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkq\n-----END PRIVATE KEY-----",
                }
            },
        )

        with pytest.raises(ConfigError, match="secret|plaintext"):
            load_config(config_dir)

    def test_secret_ref_allowed(self, tmp_path):
        """Secret references (not values) are allowed."""
        config_dir = make_minimal_config(
            tmp_path, secrets={"oauth_client_secret": {"ref": "alice/prod/github_secret"}}
        )

        # Should not raise
        config = load_config(config_dir)
        assert config is not None


class TestHttpRejectedInProduction:
    """HTTP URLs rejected in production lane."""

    def test_http_domain_in_production_rejected(self, tmp_path):
        """HTTP domain in production config rejected."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()

        platform = {"services": {"oauth": {"type": "oauth"}}}
        (config_dir / "platform.yaml").write_text(yaml.dump(platform))

        domains = {
            "oauth.example.com": {
                "service": "oauth",
                "protocol": "http",  # HTTP in production is forbidden
            }
        }
        (config_dir / "domains.yaml").write_text(yaml.dump(domains))

        production = {"lanes": {"production": {"sign_in": "github", "services": ["oauth"]}}}
        (config_dir / "production.yaml").write_text(yaml.dump(production))

        with pytest.raises(ConfigError, match="http|production"):
            load_config(config_dir)

    def test_https_allowed_in_production(self, tmp_path):
        """HTTPS domain in production is allowed."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()

        platform = {"services": {"oauth": {"type": "oauth"}}}
        (config_dir / "platform.yaml").write_text(yaml.dump(platform))

        domains = {"oauth.example.com": {"service": "oauth", "protocol": "https"}}
        (config_dir / "domains.yaml").write_text(yaml.dump(domains))

        production = {"lanes": {"production": {"sign_in": "github", "services": ["oauth"]}}}
        (config_dir / "production.yaml").write_text(yaml.dump(production))

        # Should not raise
        config = load_config(config_dir)
        assert config is not None

    def test_http_allowed_in_test(self, tmp_path):
        """HTTP domain in test lane is allowed."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()

        platform = {"services": {"oauth": {"type": "oauth"}}}
        (config_dir / "platform.yaml").write_text(yaml.dump(platform))

        domains = {
            "oauth.test.local": {
                "service": "oauth",
                "protocol": "http",  # OK in test
            }
        }
        (config_dir / "domains.yaml").write_text(yaml.dump(domains))

        test_config = {"lanes": {"test": {"sign_in": "passphrase", "services": ["oauth"]}}}
        (config_dir / "test.yaml").write_text(yaml.dump(test_config))

        # Should not raise
        config = load_config(config_dir)
        assert config is not None


class TestServiceReferences:
    """Service references must point to existing services."""

    def test_domain_references_nonexistent_service(self, tmp_path):
        """Domain referencing nonexistent service rejected."""
        config_dir = make_minimal_config(
            tmp_path, domains={"chrome.example.com": {"service": "nonexistent"}}
        )

        with pytest.raises(ConfigError, match="nonexistent|not found"):
            load_config(config_dir)

    def test_service_not_in_any_lane(self, tmp_path):
        """Service defined but not deployed in any lane is flagged."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()

        platform = {"services": {"oauth": {"type": "oauth"}, "unused_service": {"type": "chrome"}}}
        (config_dir / "platform.yaml").write_text(yaml.dump(platform))

        production = {"lanes": {"production": {"sign_in": "github", "services": ["oauth"]}}}
        (config_dir / "production.yaml").write_text(yaml.dump(production))

        with pytest.raises(ConfigError, match="unused|undeployed"):
            load_config(config_dir)


class TestLaneInvariants:
    """Lane-specific rules must be enforced."""

    def test_production_must_use_github_sign_in(self, tmp_path):
        """Production lane must have GitHub sign_in."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()

        platform = {"services": {"oauth": {"type": "oauth"}}}
        (config_dir / "platform.yaml").write_text(yaml.dump(platform))

        production = {"lanes": {"production": {"sign_in": "passphrase", "services": ["oauth"]}}}
        (config_dir / "production.yaml").write_text(yaml.dump(production))

        with pytest.raises(ConfigError, match="production|github"):
            load_config(config_dir)

    def test_test_lane_no_github_secrets(self, tmp_path):
        """Test lane cannot reference GitHub secrets."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()

        platform = {"services": {"oauth": {"type": "oauth"}}}
        (config_dir / "platform.yaml").write_text(yaml.dump(platform))

        test_config = {
            "lanes": {
                "test": {
                    "sign_in": "passphrase",
                    "services": ["oauth"],
                    "oauth_client_secret_ref": "alice/prod/github_secret",  # prod secret in test
                }
            }
        }
        (config_dir / "test.yaml").write_text(yaml.dump(test_config))

        with pytest.raises(ConfigError, match="test|github|secret"):
            load_config(config_dir)

    def test_lanes_cannot_share_secrets(self, tmp_path):
        """Different lanes cannot share the same secret reference."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()

        platform = {"services": {"oauth": {"type": "oauth"}}}
        (config_dir / "platform.yaml").write_text(yaml.dump(platform))

        secrets = {
            "github_client_secret": {"ref": "alice/shared/secret"}  # Shared ref
        }
        (config_dir / "secrets.yaml").write_text(yaml.dump(secrets))

        production = {
            "lanes": {
                "production": {
                    "sign_in": "github",
                    "services": ["oauth"],
                    "oauth_secret_ref": "alice/shared/secret",
                }
            }
        }
        (config_dir / "production.yaml").write_text(yaml.dump(production))

        test_config = {
            "lanes": {
                "test": {
                    "sign_in": "passphrase",
                    "services": ["oauth"],
                    "oauth_secret_ref": "alice/shared/secret",  # Same ref
                }
            }
        }
        (config_dir / "test.yaml").write_text(yaml.dump(test_config))

        with pytest.raises(ConfigError, match="shared|lane"):
            load_config(config_dir)


class TestIntegration:
    """End-to-end config loading with all rules."""

    def test_complete_valid_config(self, tmp_path):
        """Complete valid config with all files loads."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()

        # Define services
        platform = {
            "services": {
                "oauth": {"type": "oauth", "depends_on": []},
                "chrome": {"type": "chrome", "depends_on": ["oauth"]},
            }
        }
        (config_dir / "platform.yaml").write_text(yaml.dump(platform))

        # Define domains
        domains = {
            "oauth.example.com": {"service": "oauth", "protocol": "https"},
            "chrome.example.com": {"service": "chrome", "protocol": "https"},
        }
        (config_dir / "domains.yaml").write_text(yaml.dump(domains))

        # Production lane
        production = {
            "lanes": {"production": {"sign_in": "github", "services": ["oauth", "chrome"]}}
        }
        (config_dir / "production.yaml").write_text(yaml.dump(production))

        # Test lane
        test_config = {
            "lanes": {"test": {"sign_in": "passphrase", "services": ["oauth", "chrome"]}}
        }
        (config_dir / "test.yaml").write_text(yaml.dump(test_config))

        # Secrets (only refs, no values)
        secrets = {"github_client_secret": {"ref": "alice/prod/github_secret"}}
        (config_dir / "secrets.yaml").write_text(yaml.dump(secrets))

        # Storage config
        storage = {"chrome_state": {"path": "/var/lib/chrome-state"}}
        (config_dir / "storage.yaml").write_text(yaml.dump(storage))

        # Load all
        config = load_config(config_dir)
        assert config is not None
        assert len(config["services"]) == 2
        assert "production" in config["lanes"]
        assert "test" in config["lanes"]


def test_core_platform_config_excludes_product_and_lab_experiments():
    config = load_config(Path("config/alice"))

    services = config["services"]
    assert "dota-commentator" not in services
    assert "agent-shell" not in services

    serialized = json.dumps(config, sort_keys=True).lower()
    for forbidden in ("dota", "steam", "openai", "stripe", "gpu", "agent-shell"):
        assert forbidden not in serialized
