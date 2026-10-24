"""Negative controls for provider-wide coverage and incompatible request fields."""

import copy
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "yandex_ultra_profiles", ROOT / "scripts/validate_yandex_ultra_profiles.py"
)
validator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validator)


class YandexUltraTest(unittest.TestCase):
    def setUp(self):
        base = ROOT / "config/model_profiles"
        self.catalog = validator.read_json(base / "yandex-ultra-profiles.json")
        self.legacy = validator.read_json(base / "ultra-profiles.json")

    def validate(self):
        return validator.validate_catalog(self.catalog, self.legacy)

    def test_all_public_model_surfaces_and_legacy_are_covered(self):
        result = self.validate()
        counts = result["counts_by_surface"]
        for surface, count in {
            "base_text": 10,
            "base_realtime": 3,
            "base_image": 1,
            "embedding": 4,
            "classifier": 2,
            "dedicated": 5,
            "batch_text": 21,
            "batch_multimodal": 8,
            "retired_base": 3,
            "account_template": 3,
            "speechkit_stt": 6,
            "speechkit_tts": 4,
        }.items():
            self.assertEqual(counts[surface], count)
        self.assertEqual(result["provider_acceptance"], "NOT_RUN")

    def test_missing_new_model_is_not_hidden_by_legacy_completeness(self):
        del self.catalog["profiles"]["base:speech-realtime-max-tts-live"]
        with self.assertRaisesRegex(validator.CatalogError, "coverage"):
            self.validate()

    def test_foreign_provider_or_endpoint_is_rejected(self):
        baseline = copy.deepcopy(self.catalog)
        p = self.catalog["profiles"]["base:aliceai-image-art-3.0"]
        p["provider"] = "openai"
        with self.assertRaisesRegex(validator.CatalogError, "foreign provider"):
            self.validate()
        self.catalog = baseline
        self.catalog["profiles"]["base:aliceai-image-art-3.0"]["plan"]["endpoint"] = (
            "https://api.openai.com/v1/images/generations"
        )
        with self.assertRaisesRegex(validator.CatalogError, "non-Yandex"):
            self.validate()

    def test_unverified_activation_and_acceptance_are_rejected(self):
        for field, value in [("activation", "ACTIVE"), ("provider_acceptance", "VERIFIED")]:
            with self.subTest(field=field):
                baseline = copy.deepcopy(self.catalog)
                self.catalog["profiles"]["base:aliceai-image-art-3.0"][field] = value
                with self.assertRaises(validator.CatalogError):
                    self.validate()
                self.catalog = baseline

    def test_original_async_maximum_cannot_be_silently_lowered(self):
        self.catalog["profiles"]["base:yandexgpt-5.1"]["plan"]["wire_parameters"][
            "completionOptions"
        ]["maxTokens"] = "8192"
        with self.assertRaisesRegex(validator.CatalogError, "legacy profile drift"):
            self.validate()

    def test_image_quality_high_is_not_a_supported_ultra_parameter(self):
        plan = self.catalog["profiles"]["base:aliceai-image-art-3.0"]["plan"]
        plan["prepared_request_templates"]["landscape"]["quality"] = "high"
        with self.assertRaisesRegex(validator.CatalogError, "image unsupported"):
            self.validate()

    def test_tts_only_does_not_inherit_dialogue_tools_or_audio_input(self):
        session = self.catalog["profiles"]["base:speech-realtime-tts-live"]["plan"][
            "prepared_event_template"
        ]["session"]
        session["tools"] = [{"type": "web_search"}]
        with self.assertRaisesRegex(validator.CatalogError, "TTS-only"):
            self.validate()

    def test_livetts_speed_is_rejected_despite_generic_api_schema(self):
        session = self.catalog["profiles"]["base:speech-realtime-max-tts-live"]["plan"][
            "prepared_event_template"
        ]["session"]
        session["audio"]["output"]["speed"] = 1.5
        with self.assertRaisesRegex(validator.CatalogError, "LiveTTS speed"):
            self.validate()

    def test_embedding_input_source_conflict_cannot_be_erased(self):
        self.catalog["profiles"]["embedding:text-embeddings-v2-doc"]["plan"]["input_max_tokens"] = (
            8192
        )
        with self.assertRaisesRegex(validator.CatalogError, "input limit conflict"):
            self.validate()

    def test_source_conflict_evidence_must_be_preserved(self):
        self.catalog["source_conflicts"] = []
        with self.assertRaisesRegex(validator.CatalogError, "conflict record"):
            self.validate()

    def test_rest_embedding_variant_cannot_silently_downgrade(self):
        self.catalog["profiles"]["embedding:text-embeddings-v2-doc"]["plan"]["alternative_rest"][
            "request_template"
        ]["dim"] = "256"
        with self.assertRaisesRegex(validator.CatalogError, "REST dimension"):
            self.validate()

    def test_classifier_limits_and_tuned_fields_are_checked(self):
        self.catalog["profiles"]["classifier:classifier-yandexgpt"]["plan"]["samples"][
            "maximum_total_tokens"
        ] = 8000
        with self.assertRaisesRegex(validator.CatalogError, "classifier sample"):
            self.validate()

    def test_tuned_embedding_does_not_inherit_v2_dimensions(self):
        self.catalog["profiles"]["template:fine-tuned-text-embeddings"]["plan"]["dimensions"][
            "selected"
        ] = 768
        with self.assertRaisesRegex(validator.CatalogError, "invented tuned dimensions"):
            self.validate()

    def test_deferred_stt_cannot_be_offered_as_streaming(self):
        self.catalog["profiles"]["speechkit-stt:deferred-general:rc"]["plan"][
            "streaming_supported"
        ] = True
        with self.assertRaisesRegex(validator.CatalogError, "async only"):
            self.validate()

    def test_tts_unsafe_mode_is_not_selected_to_claim_ultra(self):
        self.catalog["profiles"]["speechkit-tts:livetts"]["plan"]["primary_transport"][
            "request_body_template"
        ]["unsafeMode"] = True
        with self.assertRaisesRegex(validator.CatalogError, "unsafe mode"):
            self.validate()

    def test_embeddings_cannot_inherit_llm_reasoning(self):
        self.catalog["profiles"]["embedding:text-embeddings-v2-query"]["plan"]["request_template"][
            "reasoning"
        ] = {"effort": "high"}
        with self.assertRaisesRegex(validator.CatalogError, "embedding foreign"):
            self.validate()

    def test_dedicated_context_is_not_copied_to_batch(self):
        self.catalog["profiles"]["yandex-batch:multimodal:qwen2.5-vl-32b-instruct"]["plan"][
            "context_window_tokens"
        ] = 32768
        with self.assertRaisesRegex(validator.CatalogError, "batch context drift"):
            self.validate()

    def test_disabled_batch_cannot_be_enabled_by_a_data_edit(self):
        profile = self.catalog["profiles"]["yandex-batch:text:qwq-32b"]
        profile["status"] = "prepared_not_activated"
        profile["plan"]["status"] = "prepared_not_activated"
        with self.assertRaisesRegex(validator.CatalogError, "disabled service unblocked"):
            self.validate()

    def test_dedicated_instance_id_is_not_invented_from_batch_uri(self):
        self.catalog["profiles"]["yandex-dedicated:qwen2.5-vl-32b-instruct"][
            "model_uri_template"
        ] = "gpt://${YANDEX_PROJECT_ID}/qwen2.5-vl-32b-instruct/"
        with self.assertRaisesRegex(validator.CatalogError, "invented dedicated URI"):
            self.validate()

    def test_account_template_is_not_counted_as_a_discovered_model(self):
        self.catalog["profiles"]["template:fine-tuned-text-embeddings"]["model_id"] = "invented"
        with self.assertRaisesRegex(validator.CatalogError, "not a deployed model"):
            self.validate()

    def test_post_retries_stay_disabled(self):
        self.catalog["policy"]["automatic_post_retries"] = 1
        with self.assertRaisesRegex(validator.CatalogError, "POST retries"):
            self.validate()

    def test_envelope_and_plan_cannot_disagree(self):
        baseline = copy.deepcopy(self.catalog)
        changes = [
            ("yandex-batch:text:qwq-32b", False, "model_id", "different-model"),
            ("yandex-dedicated:qwen2.5-7b-instruct", True, "status", "active"),
            ("yandex-dedicated:qwen2.5-7b-instruct", True, "model_uri", "gpt://invented/model"),
        ]
        for key, nested, field, value in changes:
            with self.subTest(field=field):
                self.catalog = copy.deepcopy(baseline)
                record = self.catalog["profiles"][key]
                if nested:
                    record = record["plan"]
                record[field] = value
                with self.assertRaises(validator.CatalogError):
                    self.validate()

    def test_empty_forbidden_list_cannot_allow_embedding_tools(self):
        plan = self.catalog["profiles"]["embedding:text-embeddings-v2-doc"]["plan"]
        plan["forbidden_fields"] = []
        plan["request_template"]["tools"] = [{"type": "web_search"}]
        with self.assertRaisesRegex(validator.CatalogError, "embedding foreign"):
            self.validate()


if __name__ == "__main__":
    unittest.main()
