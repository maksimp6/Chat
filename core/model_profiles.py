"""Runtime model catalog and explicitly selected request profiles.

The prepared Ultra catalog supplies the existing default settings and public
summaries. Optima is an explicit per-request preset: it controls reasoning and
truncation while preserving the chosen model, context, tools and output budget.
Local preset availability does not establish provider acceptance. Registry entries
marked ``available: False`` stay listed for accounting but cannot be selected.
"""

from __future__ import annotations

import json
import logging
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any

from core.config import ALL_MODELS, canonical_model_key

logger = logging.getLogger(__name__)

CATALOG_PATH = (
    Path(__file__).resolve().parents[1] / "config" / "model_profiles" / "ultra-profiles.json"
)
CATALOG_SCHEMA_VERSION = 1
OPTIMA_CATALOG_PATH = CATALOG_PATH.with_name("optima-profiles.json")


class ModelProfileError(ValueError):
    """A requested profile cannot be applied to this model."""


def _validated_optima_profiles(catalog: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(catalog, dict) or catalog.get("schema_version") != CATALOG_SCHEMA_VERSION:
        raise ValueError("unsupported Optima catalog schema")
    profiles = catalog.get("profiles")
    if not isinstance(profiles, dict) or not profiles:
        raise ValueError("empty Optima catalog")
    for key, profile in profiles.items():
        model = ALL_MODELS.get(key, {})
        if (
            model.get("provider", "yandex") != "yandex"
            or model.get("type") != "text"
            or not isinstance(profile, dict)
            or profile.get("model_key") != key
            or profile.get("profile_id") != f"optima-{key}"
            or profile.get("transport") != "responses"
        ):
            raise ValueError(f"invalid Optima profile identity: {key}")
        wire = profile.get("wire_parameters")
        expected: dict[str, Any] = {"truncation": "disabled"}
        if key in {"gpt-oss-120b", "gpt-oss-20b"}:
            expected["reasoning"] = {"effort": "medium"}
        if wire != expected:
            raise ValueError(f"unsupported Optima request parameters: {key}")
    return profiles


@lru_cache(maxsize=1)
def optima_profiles() -> dict[str, dict[str, Any]]:
    """Load local Optima presets; this never contacts or activates a provider."""
    try:
        text = OPTIMA_CATALOG_PATH.read_text(encoding="utf-8")
        catalog = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
        return _validated_optima_profiles(catalog)
    except (OSError, TypeError, ValueError) as exc:
        logger.warning("Optima model profiles ignored: %s", exc)
        return {}


def selected_model_profile(model_key: str, params: dict[str, Any]) -> dict[str, Any] | None:
    """Resolve an explicit profile, failing before a request instead of falling back."""
    selection = params.get("model_profile")
    if selection in (None, "", "custom"):
        return None
    if selection != "optima":
        raise ModelProfileError("Неизвестный профиль модели. Выберите текущие настройки или Optima.")
    key = canonical_model_key(model_key) if isinstance(model_key, str) else None
    profile = optima_profiles().get(key)
    model = ALL_MODELS.get(key, {})
    if (
        profile is None
        or model.get("available") is False
        or model.get("provider", "yandex") != "yandex"
        or key in completion_model_keys()
    ):
        raise ModelProfileError(
            "Optima недоступна для выбранной модели. Выберите другую модель или текущие настройки."
        )
    return profile


def apply_request_profile(model_key: str, params: dict[str, Any] | None) -> dict[str, Any]:
    """Copy params and apply the selected preset without changing model, tools or input.

    Optima owns reasoning and truncation. An explicit output limit is preserved;
    absent output limits remain absent. Unselected requests retain Ultra defaults.
    """
    result = dict(params or {})
    profile = selected_model_profile(model_key, result)
    result.pop("model_profile", None)
    if profile is not None:
        result.pop("reasoning", None)
        result.pop("reasoning_effort", None)
        result.update(deepcopy(profile["wire_parameters"]))
    else:
        for name, value in ultra_request_defaults(model_key).items():
            if result.get(name) in (None, ""):
                result[name] = value
    return result


def optima_profile_summary(profile: dict[str, Any]) -> dict[str, Any]:
    """Expose preset availability separately from unperformed provider acceptance."""
    return {
        "profile_id": profile["profile_id"],
        "status": "available",
        "transport": profile["transport"],
        "reasoning_effort": (profile["wire_parameters"].get("reasoning") or {}).get("effort"),
        "context_window_tokens": profile.get("context_window_tokens"),
        "stop_and_recheck_on": profile.get("stop_and_recheck_on"),
        "provider_acceptance": "NOT_RUN",
    }


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _validated_profiles(catalog: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(catalog, dict) or catalog.get("schema_version") != CATALOG_SCHEMA_VERSION:
        raise ValueError("unsupported Ultra catalog schema")
    profiles = catalog.get("profiles")
    yandex_models = {
        key for key, model in ALL_MODELS.items() if model.get("provider", "yandex") == "yandex"
    }
    if not isinstance(profiles, dict) or set(profiles) != yandex_models:
        raise ValueError("Ultra catalog does not cover the model registry exactly")
    for key, profile in profiles.items():
        if not isinstance(profile, dict) or profile.get("model_key") != key:
            raise ValueError(f"Ultra profile identity mismatch: {key}")
        if not isinstance(profile.get("blockers"), list):
            raise TypeError(f"Ultra profile has no blocker list: {key}")
    return profiles


@lru_cache(maxsize=1)
def ultra_profiles() -> dict[str, dict[str, Any]]:
    """Return profiles keyed by registry key, or ``{}`` when the catalog is unusable."""
    try:
        text = CATALOG_PATH.read_text(encoding="utf-8")
        catalog = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
        return _validated_profiles(catalog)
    except (OSError, TypeError, ValueError) as exc:
        logger.warning("Ultra model profiles ignored: %s", exc)
        return {}


def profile_summary(profile: dict[str, Any]) -> dict[str, Any]:
    """Return the public view of one profile, without wire templates or cost data."""
    context = profile.get("context") or {}
    output = profile.get("output") or {}
    lifecycle = profile.get("lifecycle") or {}
    return {
        "profile_id": profile.get("profile_id"),
        "status": profile.get("status"),
        "transport": profile.get("transport"),
        "lifecycle_state": lifecycle.get("state"),
        "stop_and_recheck_on": lifecycle.get("stop_and_recheck_on"),
        "context_window_tokens": context.get("window_tokens"),
        "output_ceiling_tokens": output.get("documented_hosted_ceiling_tokens"),
        "blockers": list(profile["blockers"]),
    }


def ultra_request_defaults(model_key: str) -> dict[str, Any]:
    """Return the Ultra defaults for a Responses request that the caller did not set.

    Only ``truncation`` and ``reasoning_effort`` are applied. No output ceiling is set,
    because the catalog publishes no ``documented_hosted_ceiling_tokens`` for any model.
    """
    profile = ultra_profiles().get(model_key)
    if profile is None or profile.get("transport") != "responses":
        # Completion-API models (text_generation_async, e.g. yandexgpt-5.1) keep their own wire shape.
        return {}
    wire = profile.get("wire_parameters") or {}
    defaults: dict[str, Any] = {}
    if wire.get("truncation") == "disabled":
        defaults["truncation"] = "disabled"
    effort = (wire.get("reasoning") or {}).get("effort")
    if effort:
        defaults["reasoning_effort"] = effort
    return defaults


def completion_model_keys() -> set[str]:
    """Registry keys whose Ultra profile uses the completion API, not Responses."""
    return {
        key
        for key, profile in ultra_profiles().items()
        if profile.get("transport") == "text_generation_async"
    }


def annotate_catalog(catalog: dict[str, Any]) -> dict[str, Any]:
    """Copy a model catalog with availability and optional Ultra/Optima summaries."""
    profiles = ultra_profiles()
    optima = optima_profiles()
    completion_keys = {
        key for key, profile in profiles.items() if profile.get("transport") == "text_generation_async"
    }
    annotated = deepcopy(catalog)
    for section in ("text", "voice"):
        for key, entry in (annotated.get(section) or {}).items():
            if not isinstance(entry, dict):
                continue
            if ALL_MODELS.get(key, {}).get("available") is False:
                entry["available"] = False
            profile = profiles.get(key)
            if profile is not None:
                entry["ultra_profile"] = profile_summary(profile)
            if key in optima and entry.get("available") is not False and key not in completion_keys:
                entry["optima_profile"] = optima_profile_summary(optima[key])
    # Completion-API models are listed apart; the chat picker only offers Responses models.
    text = annotated.get("text")
    if isinstance(text, dict):
        completion = {key: text.pop(key) for key in list(text) if key in completion_keys}
        if completion:
            annotated["completion"] = completion
    return annotated
