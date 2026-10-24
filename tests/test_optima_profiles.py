"""Offline Optima request/catalog checks; no provider credentials or network."""

from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from core.config import ALL_MODELS
from core import model_profiles
from yandex_client_modules.request_builder import build_response_payload


OPTIMA_KEYS = {
    "logos-runtime", "logos-runtime-flash", "yandexgpt-5-pro", "yandexgpt-5-lite",
    "deepseek-v4.1-flash", "deepseek-v4-flash", "gpt-oss-120b", "gpt-oss-20b",
    "qwen3.6-35b-a3b",
}


class OptimaProfileTests(unittest.TestCase):
    def tearDown(self):
        model_profiles.optima_profiles.cache_clear()

    def test_nine_existing_yandex_chat_models_have_explicit_presets(self):
        profiles = model_profiles.optima_profiles()
        self.assertEqual(set(profiles), OPTIMA_KEYS)
        for key, profile in profiles.items():
            with self.subTest(model=key):
                self.assertEqual(profile["model_key"], key)
                self.assertEqual(ALL_MODELS[key].get("provider", "yandex"), "yandex")
                self.assertIsNot(ALL_MODELS[key].get("available"), False)

    def test_public_catalog_adds_optima_without_mutating_input_or_hiding_ultra(self):
        original = {
            "text": {key: {"name": key} for key in (*OPTIMA_KEYS, "yandexgpt-5.1", "qwen3-235b-a22b-fp8", "claude-sonnet-4-5")},
            "voice": {"speech-realtime-deepseek-v4-flash": {"name": "Speech"}},
        }
        before = deepcopy(original)
        result = model_profiles.annotate_catalog(original)
        for key in OPTIMA_KEYS:
            with self.subTest(model=key):
                summary = result["text"][key]["optima_profile"]
                self.assertEqual(summary["status"], "available")
                self.assertEqual(summary["provider_acceptance"], "NOT_RUN")
                self.assertNotIn("wire_parameters", summary)
                self.assertIn("ultra_profile", result["text"][key])
        self.assertNotIn("optima_profile", result["completion"]["yandexgpt-5.1"])
        self.assertNotIn("optima_profile", result["text"]["qwen3-235b-a22b-fp8"])
        self.assertNotIn("optima_profile", result["text"]["claude-sonnet-4-5"])
        self.assertNotIn("optima_profile", result["voice"]["speech-realtime-deepseek-v4-flash"])
        self.assertEqual(original, before)

    def test_provider_unavailable_entry_does_not_advertise_optima(self):
        result = model_profiles.annotate_catalog({"text": {"gpt-oss-20b": {"available": False}}})
        self.assertNotIn("optima_profile", result["text"]["gpt-oss-20b"])

    def test_gpt_oss_uses_medium_instead_of_the_existing_ultra_high(self):
        for key in ("gpt-oss-120b", "gpt-oss-20b"):
            with self.subTest(model=key):
                optima = build_response_payload("folder", key, "hello", {"model_profile": "optima"})
                previous = build_response_payload("folder", key, "hello")
                self.assertEqual(optima["reasoning"], {"effort": "medium"})
                self.assertEqual(previous["reasoning"], {"effort": "high"})
                self.assertNotIn("max_output_tokens", optima)

    def test_optima_owns_reasoning_and_truncation_even_with_old_manual_values(self):
        for key in OPTIMA_KEYS:
            with self.subTest(model=key):
                params = {"model_profile": "optima", "reasoning": {"effort": "high"},
                          "reasoning_effort": "high", "truncation": "auto"}
                original = deepcopy(params)
                payload = build_response_payload("folder", key, "hello", params)
                self.assertEqual(payload["truncation"], "disabled")
                if key.startswith("gpt-oss-"):
                    self.assertEqual(payload["reasoning"], {"effort": "medium"})
                else:
                    self.assertNotIn("reasoning", payload)
                self.assertNotIn("model_profile", payload)
                self.assertNotIn("reasoning_effort", payload)
                self.assertEqual(params, original)

    def test_profile_preserves_explicit_output_input_tools_sampling_and_model(self):
        params = {
            "model_profile": "optima", "max_output_tokens": 4096,
            "input": [{"role": "user", "content": "Full context"}],
            "temperature": 0.3, "top_p": 0.8,
            "tools": [{"type": "function", "name": "read_file", "parameters": {"type": "object"}}],
            "instructions": "Keep the complete instructions.",
        }
        payload = build_response_payload("folder", "qwen3.6-35b-a3b", "hello", params)
        self.assertEqual(payload["model"], "gpt://folder/qwen3.6-35b-a3b/latest")
        for name in ("max_output_tokens", "input", "temperature", "top_p", "tools", "instructions"):
            self.assertEqual(payload[name], params[name])

    def test_unsupported_model_and_unknown_profile_are_rejected_without_fallback(self):
        unsupported = ["yandexgpt-5.1", "qwen3-235b-a22b-fp8", "speech-realtime-deepseek-v4-flash", "claude-opus-5-5", "unknown", None]
        for key in unsupported:
            with self.subTest(model=key), self.assertRaises(model_profiles.ModelProfileError):
                build_response_payload("folder", key, "hello", {"model_profile": "optima"})
        for selection in ("Optima", "unknown", [], {}):
            with self.subTest(selection=selection), self.assertRaises(model_profiles.ModelProfileError):
                build_response_payload("folder", "gpt-oss-20b", "hello", {"model_profile": selection})

    def test_custom_profile_keeps_existing_user_overrides(self):
        payload = build_response_payload("folder", "gpt-oss-20b", "hello", {
            "model_profile": "custom", "reasoning_effort": "low",
            "truncation": "auto", "max_output_tokens": 1024,
        })
        self.assertEqual(payload["reasoning"], {"effort": "low"})
        self.assertEqual(payload["truncation"], "auto")
        self.assertEqual(payload["max_output_tokens"], 1024)
        self.assertNotIn("model_profile", payload)

    def test_registry_retirement_after_cache_load_prevents_profile_use(self):
        model_profiles.optima_profiles()
        retired = {**ALL_MODELS["gpt-oss-20b"], "available": False}
        with patch.dict(ALL_MODELS, {"gpt-oss-20b": retired}):
            with self.assertRaises(model_profiles.ModelProfileError):
                build_response_payload("folder", "gpt-oss-20b", "hello", {"model_profile": "optima"})

    def test_one_retirement_does_not_disable_other_models_after_cache_reload(self):
        retired = {**ALL_MODELS["gpt-oss-20b"], "available": False}
        with patch.dict(ALL_MODELS, {"gpt-oss-20b": retired}):
            model_profiles.optima_profiles.cache_clear()
            with self.assertRaises(model_profiles.ModelProfileError):
                build_response_payload("folder", "gpt-oss-20b", "hello", {"model_profile": "optima"})
            payload = build_response_payload("folder", "qwen3.6-35b-a3b", "hello", {"model_profile": "optima"})
            self.assertEqual(payload["truncation"], "disabled")
            annotated = model_profiles.annotate_catalog({"text": {"gpt-oss-20b": {}, "qwen3.6-35b-a3b": {}}})
            self.assertNotIn("optima_profile", annotated["text"]["gpt-oss-20b"])
            self.assertIn("optima_profile", annotated["text"]["qwen3.6-35b-a3b"])

    def test_corrupt_catalog_does_not_fall_back_to_ultra(self):
        model_profiles.optima_profiles.cache_clear()
        with patch.object(type(model_profiles.OPTIMA_CATALOG_PATH), "read_text", return_value='{"schema_version":1,"schema_version":1}'):
            self.assertEqual(model_profiles.optima_profiles(), {})
            with self.assertRaises(model_profiles.ModelProfileError):
                build_response_payload("folder", "gpt-oss-20b", "hello", {"model_profile": "optima"})

    def test_catalog_cannot_enable_unsupported_qwen_depth_or_hidden_output_limit(self):
        catalog = json.loads(model_profiles.OPTIMA_CATALOG_PATH.read_text())
        for wire in ({"truncation": "disabled", "reasoning": {"effort": "medium"}},
                     {"truncation": "disabled", "max_output_tokens": 2000}):
            broken = deepcopy(catalog)
            broken["profiles"]["qwen3.6-35b-a3b"]["wire_parameters"] = wire
            with self.subTest(wire=wire), self.assertRaises(ValueError):
                model_profiles._validated_optima_profiles(broken)


if __name__ == "__main__":
    unittest.main()
