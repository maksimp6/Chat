import unittest
from unittest.mock import patch

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
                "temperature": 0.2,
                "top_p": 0.8,
                "max_output_tokens": 100,
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
        self.assertEqual(validate_generation_params({"temperature": 2})["temperature"], 2.0)

    def test_temperature_above_maximum_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "between 0 and 2"):
            validate_generation_params({"temperature": 2.1})

    def test_temperature_below_minimum_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "between 0 and 2"):
            validate_generation_params({"temperature": -0.1})

    def test_temperature_non_numeric_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "must be a finite number"):
            validate_generation_params({"temperature": "not-a-number"})

    def test_background_store_conflict_is_rejected_instead_of_overridden(self):
        with self.assertRaisesRegex(ValueError, "background=true requires store=true"):
            build_response_payload(
                "project", "model", "hello",
                params={"background": True, "store": False},
            )

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
        self.assertEqual(payload["conversation"], "conv-yandex")
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


    def test_ultra_preserves_explicit_output_cap_and_applies_other_profile_defaults(self):
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
                self.assertEqual(payload["max_output_tokens"], 2000)
                self.assertEqual(payload["instructions"], stale["instructions"])
                self.assertEqual(payload["temperature"], 0.2)
        self.assertEqual(stale["reasoning"], {"effort": "low"})
        self.assertEqual(stale["reasoning_effort"], "medium")
        self.assertEqual(stale["max_output_tokens"], 2000)

    def test_ultra_does_not_invent_reasoning_for_other_active_chat_models(self):
        models = (
            "yandexgpt-5-lite", "yandexgpt-5-pro", "qwen3.6-35b-a3b",
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
                self.assertEqual(payload["max_output_tokens"], 512)
                self.assertEqual(payload["truncation"], "disabled")

    def test_ultra_defaults_leave_foreign_and_other_transports_unmodified(self):
        from core.model_profiles import ultra_request_defaults

        for model in (
            "unknown-model", "yandexgpt-5.1",
            "speech-realtime-250923", "qwen3-235b-a22b-fp8",
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

    def test_explicit_2000_is_kept_and_zero_omits_output_cap(self):
        with patch(
            "yandex_client_modules.request_builder.ultra_request_defaults",
            return_value={"max_output_tokens": 0, "reasoning_effort": "disabled"},
        ):
            for limit in (16, 512, 2000):
                with self.subTest(limit=limit):
                    settings = {"max_output_tokens": limit}
                    payload = build_response_payload("project", "model", "hello", params=settings)
                    self.assertEqual(payload["max_output_tokens"], limit)
                    self.assertEqual(settings, {"max_output_tokens": limit})
            payload = build_response_payload("project", "model", "hello", params={"max_output_tokens": 0})
            self.assertNotIn("max_output_tokens", payload)

    def test_tool_call_limit_zero_is_sent_and_none_is_omitted(self):
        tools = [{"type": "function", "name": "inspect"}]
        for limit in (0, 1, 100):
            with self.subTest(limit=limit):
                payload = build_response_payload(
                    "project", "model", "hello", params={"tools": tools, "max_tool_calls": limit},
                )
                self.assertEqual(payload["max_tool_calls"], limit)
        for settings in ({"tools": tools}, {"tools": tools, "max_tool_calls": None}):
            payload = build_response_payload("project", "model", "hello", params=settings)
            self.assertNotIn("max_tool_calls", payload)

    def test_photo_response_preserves_tools_and_conversation_without_replay(self):
        image = "data:image/png;base64,AAAA"
        tools = [{"type": "function", "name": "inspect"}]
        settings = {"input_images": [image], "tools": tools}
        payload = build_response_payload(
            "project", "model", "Look", params=settings, yandex_conv_id="conv-provider",
        )
        self.assertEqual(payload["input"], [{"role": "user", "content": [
            {"type": "input_text", "text": "Look"},
            {"type": "input_image", "image_url": image, "detail": "auto"},
        ]}])
        self.assertEqual(payload["tools"], tools)
        self.assertEqual(payload["conversation"], "conv-provider")
        self.assertNotIn("messages", payload)
        continuation = [{"type": "function_call_output", "call_id": "call-1", "output": "done"}]
        following = build_response_payload(
            "project", "model", "", params={**settings, "input": continuation},
            yandex_conv_id="conv-provider",
        )
        self.assertEqual(following["input"], continuation)
        self.assertEqual(following["tools"], tools)
        self.assertEqual(following["conversation"], "conv-provider")

    def test_photo_input_can_omit_caption(self):
        image = "data:image/png;base64,AAAA"
        payload = build_response_payload("project", "model", "", params={"input_images": [image]})
        self.assertEqual(payload["input"], [{"role": "user", "content": [
            {"type": "input_image", "image_url": image, "detail": "auto"},
        ]}])

    def test_previous_response_and_logprobs_are_forwarded_and_conflicts_rejected(self):
        payload = build_response_payload(
            "project", "model", "hello",
            params={"previous_response_id": "resp-before", "top_logprobs": 0},
        )
        self.assertEqual(payload["previous_response_id"], "resp-before")
        self.assertEqual(payload["top_logprobs"], 0)
        with self.assertRaisesRegex(ValueError, "previous_response_id cannot be combined"):
            build_response_payload(
                "project", "model", "hello", params={"previous_response_id": "resp-before"},
                yandex_conv_id="conv-provider",
            )

    def test_generation_values_are_rejected_without_coercion(self):
        invalid = (
            ("temperature", "0.2"), ("temperature", True), ("temperature", float("nan")),
            ("temperature", float("inf")), ("temperature", 10 ** 400),
            ("top_p", "0.8"), ("top_p", -0.1), ("top_p", 1.1),
            ("max_output_tokens", "2000"), ("max_output_tokens", True),
            ("max_output_tokens", 15), ("max_output_tokens", -1),
            ("max_tool_calls", "0"), ("max_tool_calls", False), ("max_tool_calls", -1),
            ("top_logprobs", 1.5), ("top_logprobs", 21),
            ("background", "false"), ("store", "false"), ("stream", True),
            ("parallel_tool_calls", "false"), ("instructions", []), ("prompt_cache_key", {}),
            ("reasoning_effort", []), ("reasoning", []), ("truncation", "unknown"),
            ("service_tier", []), ("text", []), ("text", {"verbosity": "unknown"}),
            ("text", {"format": {"type": "unknown"}}),
            ("text", {"format": {"type": "json_schema", "name": "result", "schema": []}}),
            ("tool_choice", []), ("tool_choice", "unknown"), ("tool_choice", {}),
            ("previous_response_id", ""),
        )
        for name, value in invalid:
            with self.subTest(name=name, value=value):
                with self.assertRaisesRegex(ValueError, name):
                    validate_generation_params({name: value})


if __name__ == "__main__":
    unittest.main()

