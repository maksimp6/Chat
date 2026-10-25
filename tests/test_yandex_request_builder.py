import unittest

from yandex_client_modules.metadata_validator import MetadataValidationError
from yandex_client_modules.request_builder import build_response_payload, validate_generation_params


class YandexRequestBuilderTests(unittest.TestCase):
    def test_builds_core_payload_and_options(self):
        payload = build_response_payload(
            "project",
            "model",
            "hello",
            params={
                "background": False,
                "temperature": "0.2",
                "top_p": "0.8",
                "max_output_tokens": "100",
                "prompt_cache_key": "stable",
                "reasoning_effort": "low",
            },
        )
        self.assertEqual(payload["model"], "gpt://project/model/latest")
        self.assertEqual(payload["input"], [{"role": "user", "content": "hello"}])
        self.assertFalse(payload["background"])
        self.assertTrue(payload["store"])
        self.assertEqual(payload["temperature"], 0.2)
        self.assertEqual(payload["top_p"], 0.8)
        self.assertEqual(payload["max_output_tokens"], 100)
        self.assertEqual(payload["prompt_cache_key"], "stable")
        self.assertEqual(payload["reasoning"], {"effort": "low"})

    def test_temperature_boundaries_are_valid(self):
        self.assertEqual(validate_generation_params({"temperature": 0})["temperature"], 0.0)
        self.assertEqual(validate_generation_params({"temperature": 1})["temperature"], 1.0)

    def test_temperature_above_maximum_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "between 0 and 1"):
            validate_generation_params({"temperature": 1.1})

    def test_temperature_below_minimum_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "between 0 and 1"):
            validate_generation_params({"temperature": -0.1})

    def test_temperature_non_numeric_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "must be a number"):
            validate_generation_params({"temperature": "not-a-number"})

    def test_background_forces_store(self):
        payload = build_response_payload(
            "project",
            "model",
            "hello",
            params={"background": True, "store": False},
        )
        self.assertTrue(payload["background"])
        self.assertTrue(payload["store"])

    def test_mcp_tools_are_cleaned(self):
        payload = build_response_payload(
            "project",
            "model",
            "hello",
            params={
                "background": False,
                "tools": [
                    {"type": "mcp", "server_url": " https://example.test ", "server_label": "demo"},
                    {"type": "mcp", "server_label": "invalid"},
                    {"type": "function", "name": "local"},
                ],
            },
        )
        self.assertEqual(
            payload["tools"],
            [
                {"type": "mcp", "server_label": "demo", "server_url": "https://example.test", "require_approval": "never"},
                {"type": "function", "name": "local"},
            ],
        )
        self.assertTrue(payload["parallel_tool_calls"])

    def test_remote_mcp_disables_approval_but_preserves_external_authorization(self):
        payload = build_response_payload(
            "project", "model", "hello", params={
                "background": False,
                "tools": [{
                    "type": "mcp", "connector_id": "connector_demo",
                    "authorization": "synthetic-provider-token",
                    "require_approval": "always",
                }],
            },
        )
        self.assertEqual(payload["tools"][0]["require_approval"], "never")
        self.assertEqual(payload["tools"][0]["authorization"], "synthetic-provider-token")
        self.assertEqual(payload["tools"][0]["connector_id"], "connector_demo")

    def test_conversation_and_metadata_are_preserved(self):
        payload = build_response_payload(
            "project",
            "model",
            "hello",
            params={"background": False},
            metadata={"trace_id": "trace-1"},
            conversation_id="conv-local",
            yandex_conv_id="conv-yandex",
        )
        self.assertEqual(payload["metadata"], {"trace_id": "trace-1"})
        self.assertEqual(payload["conversation"], {"id": "conv-yandex"})
        self.assertEqual(payload["prompt_cache_key"], "conv-local")

    def test_invalid_metadata_is_rejected_before_payload_is_returned(self):
        with self.assertRaises(MetadataValidationError):
            build_response_payload(
                "project",
                "model",
                "hello",
                params={"background": False},
                metadata={"trace_id": 123},
            )


    def test_ultra_replaces_old_browser_limits_and_structured_reasoning(self):
        stale = {
            "background": False,
            "max_output_tokens": 2000,
            "truncation": "auto",
            "reasoning_effort": "medium",
            "reasoning": {"effort": "low"},
            "instructions": "Keep this instruction",
            "temperature": 0.2,
        }
        for model in ("gpt-oss-120b", "gpt-oss-20b"):
            with self.subTest(model=model):
                payload = build_response_payload("project", model, "hello", params=stale)
                self.assertEqual(payload["reasoning"], {"effort": "high"})
                self.assertEqual(payload["truncation"], "disabled")
                self.assertNotIn("max_output_tokens", payload)
                self.assertEqual(payload["instructions"], stale["instructions"])
                self.assertEqual(payload["temperature"], 0.2)
        self.assertEqual(stale["reasoning"], {"effort": "low"})
        self.assertEqual(stale["reasoning_effort"], "medium")
        self.assertEqual(stale["max_output_tokens"], 2000)

    def test_ultra_does_not_invent_reasoning_for_other_active_chat_models(self):
        models = (
            "logos-runtime", "logos-runtime-flash", "yandexgpt-5-pro",
            "yandexgpt-5-lite", "deepseek-v4.1-flash", "deepseek-v4-flash",
            "qwen3.6-35b-a3b",
        )
        for model in models:
            with self.subTest(model=model):
                payload = build_response_payload(
                    "project", model, "hello", params={
                        "reasoning": {"effort": "high"},
                        "reasoning_effort": "medium",
                        "max_output_tokens": 512,
                        "truncation": "auto",
                    },
                )
                self.assertNotIn("reasoning", payload)
                self.assertNotIn("max_output_tokens", payload)
                self.assertEqual(payload["truncation"], "disabled")

    def test_ultra_defaults_leave_foreign_and_other_transports_unmodified(self):
        from core.model_profiles import ultra_request_defaults

        for model in (
            "claude-opus-5-5", "unknown-model", "yandexgpt-5.1",
            "speech-realtime-deepseek-v4-flash", "qwen3-235b-a22b-fp8",
        ):
            with self.subTest(model=model):
                self.assertEqual(ultra_request_defaults(model), {})

    def test_public_ultra_settings_match_the_enforced_oss_profile(self):
        from core.model_profiles import profile_summary, ultra_profiles

        summary = profile_summary(ultra_profiles()["gpt-oss-120b"])
        self.assertEqual(summary["default_profile"], "ultra")
        self.assertEqual(summary["settings_defaults"], {
            "max_output_tokens": 0,
            "truncation": "disabled",
            "reasoning_effort": "high",
        })
        self.assertEqual(summary["status"], "prepared_not_activated")
        self.assertTrue(summary["blockers"])


if __name__ == "__main__":
    unittest.main()
