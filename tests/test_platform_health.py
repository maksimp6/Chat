"""Tests for Alice Platform health checks (derived from config)."""

from urllib.parse import urlsplit

import pytest
from alice_platform.health import (
    generate_health_plan,
    HealthCheck,
)


class TestHealthPlanGeneration:
    """Health checks generated from config."""

    def test_health_plan_from_config(self):
        """Health checks generated from config domains and services."""
        config = {
            "services": {"oauth": {"type": "oauth"}, "chrome": {"type": "chrome"}},
            "domains": {
                "oauth.example.com": {"service": "oauth", "protocol": "https"},
                "chrome.example.com": {"service": "chrome", "protocol": "https"},
            },
            "lanes": {"test": {"sign_in": "passphrase", "services": ["oauth", "chrome"]}},
        }

        health_plan = generate_health_plan("test", config)

        # Should have checks for both domains
        assert len(health_plan) >= 2

        # Check that domains are in the plan
        hosts_checked = {urlsplit(check.endpoint).hostname for check in health_plan}
        assert hosts_checked == {"oauth.example.com", "chrome.example.com"}

    def test_health_check_includes_sign_in_method(self):
        """Health check includes expected sign_in method."""
        config = {
            "services": {"oauth": {"type": "oauth"}},
            "domains": {"oauth.example.com": {"service": "oauth", "protocol": "https"}},
            "lanes": {"test": {"sign_in": "passphrase", "services": ["oauth"]}},
        }

        health_plan = generate_health_plan("test", config)

        # Should have a health check
        assert len(health_plan) > 0

        # Check should know the expected sign_in
        check = health_plan[0]
        assert check.expected_sign_in == "passphrase"

    def test_health_check_protocol_from_config(self):
        """Health check URL uses protocol from config."""
        config = {
            "services": {"oauth": {"type": "oauth"}},
            "domains": {"oauth.test.local": {"service": "oauth", "protocol": "http"}},
            "lanes": {"test": {"sign_in": "passphrase", "services": ["oauth"]}},
        }

        health_plan = generate_health_plan("test", config)

        check = health_plan[0]
        assert check.endpoint.startswith("http://")

    def test_production_health_checks_github(self):
        """Production health checks expect GitHub sign_in."""
        config = {
            "services": {"oauth": {"type": "oauth"}},
            "domains": {"oauth.example.com": {"service": "oauth", "protocol": "https"}},
            "lanes": {"production": {"sign_in": "github", "services": ["oauth"]}},
        }

        health_plan = generate_health_plan("production", config)

        assert len(health_plan) > 0
        assert health_plan[0].expected_sign_in == "github"

    def test_no_services_in_lane(self):
        """Lane with no services generates empty health plan."""
        config = {
            "services": {"oauth": {"type": "oauth"}, "chrome": {"type": "chrome"}},
            "domains": {"oauth.example.com": {"service": "oauth", "protocol": "https"}},
            "lanes": {
                "empty": {
                    "sign_in": "passphrase",
                    "services": [],  # No services
                }
            },
        }

        health_plan = generate_health_plan("empty", config)

        # Should have no checks
        assert len(health_plan) == 0

    def test_health_plan_only_includes_lane_services(self):
        """Health plan only checks services deployed in that lane."""
        config = {
            "services": {"oauth": {"type": "oauth"}, "chrome": {"type": "chrome"}},
            "domains": {
                "oauth.example.com": {"service": "oauth", "protocol": "https"},
                "chrome.example.com": {"service": "chrome", "protocol": "https"},
            },
            "lanes": {
                "test": {
                    "sign_in": "passphrase",
                    "services": ["oauth"],  # Only oauth
                },
                "production": {
                    "sign_in": "github",
                    "services": ["oauth", "chrome"],  # Both
                },
            },
        }

        test_plan = generate_health_plan("test", config)
        prod_plan = generate_health_plan("production", config)

        # Test should only have oauth
        assert len(test_plan) == 1
        assert "oauth" in [check.service for check in test_plan]

        # Production should have both
        assert len(prod_plan) == 2
        services = {check.service for check in prod_plan}
        assert "oauth" in services
        assert "chrome" in services
