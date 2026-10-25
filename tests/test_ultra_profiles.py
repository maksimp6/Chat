"""Regression checks for the published Ultra profile contract."""

import copy
import importlib.util
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "ultra_profiles", ROOT / "scripts/validate_ultra_profiles.py"
)
ultra = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ultra)


class UltraProfilesTest(unittest.TestCase):
    def setUp(self):
        self.catalog = ultra.read_json(ROOT / "config/model_profiles/ultra-profiles.json")
        self.registry = ultra.read_registry(ROOT / "core/config.py")

    def validate(self):
        return ultra.validate_catalog(self.catalog, self.registry, ROOT)

    def test_complete_registry_and_aliases(self):
        profiles = self.validate()
        self.assertEqual(len(profiles), 7)
        self.assertEqual(profiles["yandexgpt-5-lite"]["upstream_id"], "yandexgpt-lite")
        self.assertEqual(profiles["gpt-oss-20b"]["upstream_id"], "gpt-oss-20b")

    def test_new_registry_model_requires_new_profile(self):
        self.registry["new-provider-model"] = {"type": "text"}
        with self.assertRaisesRegex(ultra.ProfileError, "coverage"):
            self.validate()

    def test_missing_profile_is_rejected(self):
        del self.catalog["profiles"]["gpt-oss-20b"]
        with self.assertRaisesRegex(ultra.ProfileError, "coverage"):
            self.validate()

    def test_unknown_model_never_falls_back(self):
        with self.assertRaisesRegex(ultra.ProfileError, "no fallback"):
            ultra.require_executable_profile(self.catalog, "typo", date(2026, 10, 24))

    def test_alias_uri_cannot_silently_point_at_another_model(self):
        self.catalog["profiles"]["yandexgpt-5-lite"]["model_uri_template"] = (
            "gpt://${YANDEX_PROJECT_ID}/gpt-oss-20b/latest"
        )
        with self.assertRaisesRegex(ultra.ProfileError, "URI mismatch"):
            self.validate()

    def test_prepared_profile_cannot_be_sent(self):
        for key in self.catalog["profiles"]:
            with self.subTest(model=key):
                with self.assertRaises(ultra.ProfileError):
                    ultra.require_executable_profile(self.catalog, key, date(2026, 10, 24))

    def test_retired_ids_have_no_automatic_replacement(self):
        profiles = self.validate()
        retired = {key for key, p in profiles.items() if p["status"] == "blocked_retired"}
        self.assertEqual(
            retired,
            {
                "qwen3-235b-a22b-fp8",
                "speech-realtime-250923",
                "speech-realtime-250923",
            },
        )
        self.assertIsNone(self.catalog["policy"]["model_fallback"])

    def test_lifecycle_boundary_blocks_even_a_mutated_active_profile(self):
        p = self.catalog["profiles"]["gpt-oss-120b"]
        p["status"] = "active"
        p["blockers"] = []
        with self.assertRaisesRegex(ultra.ProfileError, "lifecycle"):
            ultra.require_executable_profile(self.catalog, "gpt-oss-120b", date(2026, 10, 30))

    def test_guessed_hosted_output_ceiling_is_rejected(self):
        p = self.catalog["profiles"]["yandexgpt-5-lite"]
        p["output"]["documented_hosted_ceiling_tokens"] = p["context"]["window_tokens"]
        with self.assertRaisesRegex(ultra.ProfileError, "undocumented"):
            self.validate()

    def test_context_cannot_be_copied_to_outbound_output(self):
        p = self.catalog["profiles"]["qwen3-235b-a22b-fp8"]
        p["wire_parameters"]["max_output_tokens"] = p["context"]["window_tokens"]
        with self.assertRaisesRegex(ultra.ProfileError, "guessed"):
            self.validate()

    def test_unknown_qwen_reasoning_wire_is_not_invented(self):
        self.catalog["profiles"]["qwen3-235b-a22b-fp8"]["wire_parameters"]["reasoning"] = {
            "effort": "high"
        }
        with self.assertRaisesRegex(ultra.ProfileError, "unsupported reasoning"):
            self.validate()

    def test_generic_xhigh_does_not_override_model_specific_high(self):
        self.catalog["profiles"]["gpt-oss-20b"]["wire_parameters"]["reasoning"] = {
            "effort": "xhigh"
        }
        with self.assertRaises(ultra.ProfileError):
            self.validate()

    def test_original_async_maximum_and_transport_remain_intact(self):
        p = self.validate()["yandexgpt-5.1"]
        self.assertEqual(p["output"]["requested"], "32768")
        self.assertEqual(p["wire_parameters"]["completionOptions"]["maxTokens"], "32768")
        self.assertFalse(p["reasoning"]["supported"])
        self.assertFalse(p["tools"]["function_calling"])

    def test_async_downgrade_to_8192_is_rejected(self):
        p = self.catalog["profiles"]["yandexgpt-5.1"]
        p["wire_parameters"]["completionOptions"]["maxTokens"] = "8192"
        with self.assertRaisesRegex(ultra.ProfileError, "drift"):
            self.validate()

    def test_realtime_uses_its_own_maximum_selector(self):
        p = self.validate()["speech-realtime-250923"]
        self.assertEqual(p["wire_parameters"]["session"]["max_output_tokens"], "inf")
        self.assertEqual(p["wire_parameters"]["session"]["yc_timeout_seconds"], 3600)
        self.assertIsNone(p["reasoning"]["supported"])

    def test_responses_fields_cannot_leak_into_realtime(self):
        p = self.catalog["profiles"]["speech-realtime-250923"]
        p["wire_parameters"]["session"]["truncation"] = "disabled"
        with self.assertRaisesRegex(ultra.ProfileError, "foreign API"):
            self.validate()

    def test_context_truncation_and_automatic_paid_retry_rejected(self):
        bad = copy.deepcopy(self.catalog)
        self.catalog["policy"]["automatic_post_retries"] = 1
        with self.assertRaisesRegex(ultra.ProfileError, "retries"):
            self.validate()
        self.catalog = bad
        self.catalog["profiles"]["yandexgpt-5-lite"]["context"]["overflow"] = "truncate"
        with self.assertRaises(ultra.ProfileError):
            self.validate()

    def test_duplicate_json_keys_are_rejected(self):
        with self.assertRaisesRegex(ultra.ProfileError, "duplicate JSON"):
            ultra._unique_object([("model", "a"), ("model", "b")])


if __name__ == "__main__":
    unittest.main()
