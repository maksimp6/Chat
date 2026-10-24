"""Official Yandex IDs reach the existing Logos keys and their Optima presets.

These tests exercise local normalization and payload construction only. Importing
the discovery class and using its static method makes no provider request.
"""

import json

import pytest

from core import config, model_profiles
from provider_accounts.model_discovery import ModelDiscovery
from yandex_client_modules.request_builder import build_response_payload


MODELS = [
    ("logos-runtime", "aliceai-llm", "logosai-llm", "Logos Runtime"),
    ("logos-runtime-flash", "aliceai-llm-flash", "logosai-llm-flash", "Logos Runtime Flash"),
]


@pytest.mark.parametrize("registry_key,upstream,legacy,name", MODELS)
@pytest.mark.parametrize("key_source", [0, 1, 2])
def test_optima_payload_uses_official_id_for_registry_provider_and_stored_keys(
    registry_key, upstream, legacy, name, key_source,
):
    model_key = (registry_key, upstream, legacy)[key_source]
    payload = build_response_payload(
        "synthetic-project", model_key, "Local fixture only.",
        params={"model_profile": "optima"},
    )

    assert payload["model"] == f"gpt://synthetic-project/{upstream}/latest"
    assert payload["truncation"] == "disabled"
    assert "reasoning" not in payload
    assert "max_output_tokens" not in payload
    assert config.get_model_uri("synthetic-project", model_key) == payload["model"]
    assert config.TEXT_MODELS[registry_key]["name"] == name


@pytest.mark.parametrize("registry_key,upstream,legacy,name", MODELS)
@pytest.mark.parametrize("suffix", ["", "/latest"])
def test_official_discovery_uris_get_the_matching_optima_summary(
    registry_key, upstream, legacy, name, suffix,
):
    entry = ModelDiscovery._normalize_model({"id": f"gpt://synthetic-project/{upstream}{suffix}"})
    assert entry is not None
    assert entry["id"] == registry_key
    assert entry["name"] == name
    assert entry["input"] == config.TEXT_MODELS[registry_key]["input"]

    catalog = model_profiles.annotate_catalog({"text": {entry["id"]: entry}, "voice": {}})
    assert catalog["text"][registry_key]["optima_profile"]["profile_id"] == f"optima-{registry_key}"
    assert "optima_profile" not in entry


@pytest.mark.parametrize("raw_id", [
    "gpt://synthetic-project", "gpt://synthetic-project/",
    "gpt://synthetic-project//latest", "gpt:///aliceai-llm/latest",
])
def test_malformed_gpt_uri_does_not_misidentify_a_folder_as_a_model(raw_id):
    assert ModelDiscovery._normalize_model({"id": raw_id}) is None


@pytest.mark.parametrize("registry_key,upstream,legacy,name", MODELS)
def test_stored_aliases_keep_registry_identity_and_price(registry_key, upstream, legacy, name):
    usage = {"input_tokens": 1500, "output_tokens": 700}
    for alias in (legacy, upstream):
        assert config.canonical_model_key(alias) == registry_key
        assert config.registry_key_for_upstream(alias) == registry_key
        assert config.calculate_full_cost(alias, usage) == config.calculate_full_cost(registry_key, usage)
        assert config.calculate_cost(alias, 1500, 700) == config.calculate_cost(registry_key, 1500, 700)
        entry = ModelDiscovery._normalize_model({"id": f"gpt://synthetic-project/{alias}/latest"})
        assert entry["id"] == registry_key


@pytest.mark.parametrize("registry_key,upstream,legacy,name", MODELS)
def test_ultra_catalog_uses_the_same_official_upstream(registry_key, upstream, legacy, name):
    profile = json.loads(model_profiles.CATALOG_PATH.read_text(encoding="utf-8"))["profiles"][registry_key]
    assert profile["upstream_id"] == upstream
    assert profile["model_uri_template"] == f"gpt://${{YANDEX_PROJECT_ID}}/{upstream}/latest"
