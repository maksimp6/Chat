import unittest

from pricing_registry import PricingRegistry, PricingSnapshot, attach_pricing_snapshot


class PricingRegistryTests(unittest.TestCase):
    def test_resolve_selects_snapshot_by_effective_date(self):
        registry = PricingRegistry([
            PricingSnapshot(
                pricing_version="yandex-2026-09",
                effective_from="2026-09-01",
                effective_to="2026-10-01",
                currency="RUB",
                source="https://example.test/pricing",
                source_updated="2026-09-01",
                provider="yandex_cloud",
                service="ai_studio",
                operation="completion",
                model="model-a",
                unit="1k_output_tokens",
                unit_price=1.0,
            ),
            PricingSnapshot(
                pricing_version="yandex-2026-10",
                effective_from="2026-10-01",
                effective_to=None,
                currency="RUB",
                source="https://example.test/pricing",
                source_updated="2026-10-01",
                provider="yandex_cloud",
                service="ai_studio",
                operation="completion",
                model="model-a",
                unit="1k_output_tokens",
                unit_price=2.0,
            ),
        ])

        old = registry.resolve(
            provider="yandex_cloud", service="ai_studio", operation="completion",
            model="model-a", effective_date="2026-09-20",
        )
        new = registry.resolve(
            provider="yandex_cloud", service="ai_studio", operation="completion",
            model="model-a", effective_date="2026-10-02",
        )
        self.assertEqual(old.pricing_version, "yandex-2026-09")
        self.assertEqual(old.unit_price, 1.0)
        self.assertEqual(new.pricing_version, "yandex-2026-10")
        self.assertEqual(new.unit_price, 2.0)

    def test_duplicate_snapshot_is_rejected(self):
        snapshot = PricingSnapshot(
            pricing_version="v1", effective_from="2026-01-01", effective_to=None,
            currency="RUB", source="https://example.test", source_updated="2026-01-01",
            provider="provider", service="service", operation="op",
        )
        registry = PricingRegistry([snapshot])
        with self.assertRaises(ValueError):
            registry.register(snapshot)

    def test_usage_retains_pricing_snapshot(self):
        snapshot = PricingSnapshot(
            pricing_version="v1", effective_from="2026-01-01", effective_to=None,
            currency="RUB", source="https://example.test", source_updated="2026-01-01",
            provider="provider", service="speechkit", operation="tts",
            unit="250_characters", unit_price=0.5,
        )
        usage = attach_pricing_snapshot(
            {"characters": 600, "billable_units": 3}, snapshot, cost=1.5
        )
        self.assertEqual(usage["cost"], 1.5)
        self.assertEqual(usage["pricing"]["pricing_version"], "v1")
        self.assertEqual(usage["pricing"]["unit_price"], 0.5)


if __name__ == "__main__":
    unittest.main()
