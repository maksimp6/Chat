"""Ultra profiles are shown in the model list without changing provider requests."""

import json

import pytest

import app as app_module
from core import model_profiles
from core.config import ALL_MODELS, calculate_cost
from provider_accounts.model_discovery import static_model_catalog

RETIRED = {"qwen3-235b-a22b-fp8", "speech-realtime-260528", "speech-realtime-250923"}


class FakeDiscovery:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error

    def get_models(self, *, force_refresh=False):
        if self.error:
            raise self.error
        return self.result


@pytest.fixture
def fresh_profiles_cache():
    model_profiles.ultra_profiles.cache_clear()
    yield
    model_profiles.ultra_profiles.cache_clear()


def test_catalog_covers_every_yandex_registry_model_exactly(fresh_profiles_cache):
    profiles = model_profiles.ultra_profiles()

    assert len(profiles) == 14
    assert set(profiles) == {
        key for key, info in ALL_MODELS.items() if info.get("provider", "yandex") == "yandex"
    }


def test_foreign_provider_does_not_invalidate_yandex_profiles(monkeypatch, fresh_profiles_cache):
    monkeypatch.setitem(ALL_MODELS, "foreign-model", {"provider": "other"})
    profiles = model_profiles.ultra_profiles()
    assert len(profiles) == 14
    annotated = model_profiles.annotate_catalog(
        {"text": {"foreign-model": {"provider": "other"}}, "voice": {}}
    )
    assert "ultra_profile" not in annotated["text"]["foreign-model"]


def test_new_yandex_model_still_requires_a_profile(monkeypatch, fresh_profiles_cache):
    monkeypatch.setitem(ALL_MODELS, "new-yandex-model", {"provider": "yandex"})
    assert model_profiles.ultra_profiles() == {}


def test_only_retired_models_are_unavailable_in_the_registry():
    for key, info in ALL_MODELS.items():
        assert info.get("available", True) is (key not in RETIRED), key


def test_retired_models_remain_priced_for_accounting():
    assert calculate_cost("qwen3-235b-a22b-fp8", 1000, 1000) > 0


def test_static_catalog_exposes_retired_models_as_unavailable():
    catalog = static_model_catalog()

    assert catalog["text"]["qwen3-235b-a22b-fp8"]["available"] is False
    assert catalog["voice"]["speech-realtime-260528"]["available"] is False
    assert catalog["text"]["yandexgpt-5.1"].get("available", True) is True


def test_summary_is_public_and_has_no_wire_templates(fresh_profiles_cache):
    catalog = model_profiles.annotate_catalog(
        {"text": {"yandexgpt-5.1": {"name": "YandexGPT 5.1 Pro"}}, "voice": {}}
    )

    summary = catalog["text"]["yandexgpt-5.1"]["ultra_profile"]
    assert summary["status"] == "prepared_not_activated"
    assert summary["context_window_tokens"] == 32768
    assert summary["output_ceiling_tokens"] is None
    assert summary["blockers"]
    assert not {"wire_parameters", "model_uri_template", "cost", "verification"} & set(summary)


def test_discovered_retired_model_is_forced_unavailable(fresh_profiles_cache):
    discovered = {
        "text": {"qwen3-235b-a22b-fp8": {"name": "Qwen3", "available": True}},
        "voice": {},
    }

    annotated = model_profiles.annotate_catalog(discovered)

    entry = annotated["text"]["qwen3-235b-a22b-fp8"]
    assert entry["available"] is False
    assert entry["ultra_profile"]["status"] == "blocked_retired"
    assert discovered["text"]["qwen3-235b-a22b-fp8"]["available"] is True


def test_unusable_catalog_fails_closed_without_summaries(
    tmp_path, monkeypatch, fresh_profiles_cache
):
    broken = json.loads(model_profiles.CATALOG_PATH.read_text(encoding="utf-8"))
    del broken["profiles"]["gpt-oss-20b"]
    path = tmp_path / "ultra-profiles.json"
    path.write_text(json.dumps(broken), encoding="utf-8")
    monkeypatch.setattr(model_profiles, "CATALOG_PATH", path)
    model_profiles.ultra_profiles.cache_clear()

    assert model_profiles.ultra_profiles() == {}
    annotated = model_profiles.annotate_catalog(
        {"text": {"yandexgpt-5.1": {"name": "YandexGPT 5.1 Pro"}}, "voice": {}}
    )
    assert "ultra_profile" not in annotated["text"]["yandexgpt-5.1"]


def test_profile_without_blocker_list_fails_closed(tmp_path, monkeypatch, fresh_profiles_cache):
    broken = json.loads(model_profiles.CATALOG_PATH.read_text(encoding="utf-8"))
    broken["profiles"]["yandexgpt-5.1"]["blockers"] = "none"
    path = tmp_path / "ultra-profiles.json"
    path.write_text(json.dumps(broken), encoding="utf-8")
    monkeypatch.setattr(model_profiles, "CATALOG_PATH", path)
    model_profiles.ultra_profiles.cache_clear()

    assert model_profiles.ultra_profiles() == {}


def test_duplicate_keys_in_catalog_fail_closed(tmp_path, monkeypatch, fresh_profiles_cache):
    path = tmp_path / "ultra-profiles.json"
    path.write_text('{"schema_version": 1, "schema_version": 1}', encoding="utf-8")
    monkeypatch.setattr(model_profiles, "CATALOG_PATH", path)
    model_profiles.ultra_profiles.cache_clear()

    assert model_profiles.ultra_profiles() == {}


def test_models_route_adds_summaries_and_marks_retired_provider_models(monkeypatch):
    discovery = FakeDiscovery(
        {
            "text": {
                "yandexgpt-5.1": {"name": "YandexGPT 5.1 Pro", "type": "text", "available": True},
                "qwen3-235b-a22b-fp8": {"name": "Qwen3", "type": "text", "available": True},
            },
            "voice": {},
        }
    )
    monkeypatch.setattr(app_module, "MODEL_DISCOVERY", discovery)

    payload = app_module.app.test_client().get("/api/models").get_json()

    assert payload["degraded"] is False
    assert payload["text"]["yandexgpt-5.1"]["available"] is True
    assert payload["text"]["yandexgpt-5.1"]["ultra_profile"]["context_window_tokens"] == 32768
    assert payload["text"]["qwen3-235b-a22b-fp8"]["available"] is False
    assert payload["text"]["qwen3-235b-a22b-fp8"]["ultra_profile"]["status"] == "blocked_retired"


def test_degraded_route_keeps_retired_voice_models_unavailable(monkeypatch):
    discovery = FakeDiscovery(error=app_module.ModelDiscoveryError("unavailable"))
    monkeypatch.setattr(app_module, "MODEL_DISCOVERY", discovery)

    payload = app_module.app.test_client().get("/api/models").get_json()

    assert payload["degraded"] is True
    assert payload["voice"]["speech-realtime-260528"]["available"] is False
    assert payload["text"]["logos-runtime"]["ultra_profile"]["blockers"]
    assert "available" not in payload["text"]["logos-runtime"]
