"""Runtime view of the prepared Ultra model profiles.

Active Yandex Responses models use their Ultra settings by default, including
requests from clients with older saved settings. The model catalog publishes the
same settings for the browser. Provider acceptance and unresolved capabilities
remain separate from this local default policy. Retired registry entries remain
listed for accounting and never receive active defaults.
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
    reasoning = profile.get("reasoning") or {}
    wire = profile.get("wire_parameters") or {}
    defaults = ultra_request_defaults(profile.get("model_key", ""))
    return {
        "profile_id": profile.get("profile_id"),
        "default_profile": "ultra" if defaults else None,
        "settings_defaults": defaults,
        "status": profile.get("status"),
        "transport": profile.get("transport"),
        "lifecycle_state": lifecycle.get("state"),
        "stop_and_recheck_on": lifecycle.get("stop_and_recheck_on"),
        "context_window_tokens": context.get("window_tokens"),
        "output_ceiling_tokens": output.get("documented_hosted_ceiling_tokens"),
        "output_selection": output.get("selection"),
        "reasoning_depth_control": reasoning.get("depth_control") is True,
        "truncation": wire.get("truncation"),
        "blockers": list(profile["blockers"]),
    }


def ultra_request_defaults(model_key: str) -> dict[str, Any]:
    """Canonical local Ultra settings for an active Yandex Responses model.

    Zero omits an application output cap; it is not a claim about the provider's
    output ceiling. Only reasoning effort present in this model's wire profile
    is selected. Unknown or unsupported reasoning is omitted from the request.
    """
    model_key = canonical_model_key(model_key)
    profile = ultra_profiles().get(model_key)
    if (
        profile is None
        or profile.get("transport") != "responses"
        or profile.get("status") == "blocked_retired"
        or ALL_MODELS.get(model_key, {}).get("available") is False
    ):
        return {}
    wire = profile.get("wire_parameters") or {}
    defaults: dict[str, Any] = {
        "max_output_tokens": 0,
        "reasoning_effort": "disabled",
    }
    if wire.get("truncation") == "disabled":
        defaults["truncation"] = "disabled"
    effort = (wire.get("reasoning") or {}).get("effort")
    if effort and (profile.get("reasoning") or {}).get("depth_control") is True:
        defaults["reasoning_effort"] = effort
    return defaults


def ultra_reasoning_controllable(model_key: str) -> bool:
    """False when the Ultra profile says the model has no reasoning-depth control.

    Such a model must not receive a ``reasoning`` field: the profile publishes no wire shape
    for it, and a requested effort can spend the whole output budget on reasoning.
    Models without a profile keep the caller's settings unchanged.
    """
    profile = ultra_profiles().get(canonical_model_key(model_key))
    if profile is None:
        return True
    return (profile.get("reasoning") or {}).get("depth_control") is not False


def completion_model_keys() -> set[str]:
    """Registry keys whose Ultra profile uses the completion API, not Responses."""
    return {
        key
        for key, profile in ultra_profiles().items()
        if profile.get("transport") == "text_generation_async"
    }


def annotate_catalog(catalog: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of a model catalog with registry availability and Ultra summaries."""
    profiles = ultra_profiles()
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
    # Completion-API models are listed apart; the chat picker only offers Responses models.
    text = annotated.get("text")
    if isinstance(text, dict):
        completion = {key: text.pop(key) for key in list(text) if key in completion_keys}
        if completion:
            annotated["completion"] = completion
    return annotated
