"""Tests for billing and revenue tracking."""

import pytest
from alice_platform.billing import calculate_service_cost, calculate_lane_cost


class TestBillingCalculation:
    """Billing calculation tests."""

    def test_calculate_service_cost_basic(self):
        """Calculate cost for basic service without GPU."""
        service_config = {"resources": {"cpu": "0.5", "memory": "512Mi", "scale": 1}}
        billing = calculate_service_cost("oauth", service_config, "test")

        assert billing.service == "oauth"
        assert billing.lane == "test"
        assert billing.cpu == "0.5"
        assert billing.memory == "512Mi"
        assert billing.min_instances == 1
        assert billing.base_cost_rub >= 0
        assert billing.gpu_cost_rub is None
        assert billing.total_cost_rub == billing.base_cost_rub

    def test_calculate_service_cost_with_gpu(self):
        """Calculate cost for service with GPU."""
        service_config = {
            "resources": {
                "cpu": "1",
                "memory": "4096Mi",
                "gpu": "1",
                "gpu_memory": "8192Mi",
                "scale": 1,
            }
        }
        billing = calculate_service_cost("dota-commentator", service_config, "production")

        assert billing.service == "dota-commentator"
        assert billing.gpu_cost_rub is not None
        assert billing.total_cost_rub > billing.base_cost_rub

    def test_calculate_lane_cost(self):
        """Calculate total cost for a lane."""
        config = {
            "services": {
                "oauth": {"resources": {"cpu": "0.1", "scale": 1}},
                "chrome": {"resources": {"cpu": "0.5", "scale": 1}},
            },
            "lanes": {"test": {"services": ["oauth", "chrome"]}},
        }
        result = calculate_lane_cost("test", config)

        assert result["lane"] == "test"
        assert result["services"] == 2
        assert result["total_rub_per_month"] > 0
        assert len(result["breakdown"]) == 2

    def test_calculate_lane_cost_empty(self):
        """Calculate cost for empty lane."""
        config = {"services": {}, "lanes": {"test": {"services": []}}}
        result = calculate_lane_cost("test", config)

        assert result["lane"] == "test"
        assert result["services"] == 0
        assert result["total_rub_per_month"] == 0.0
