"""Runtime view of the prepared Ultra model profiles.

The catalog only describes the model picker. It never changes a provider request:
every profile keeps explicit blockers until a separately reviewed adapter, provider
evidence and an owner-approved run budget exist. Registry entries marked
``available: False`` stay listed for accounting but cannot be selected.
"""

from __future__ import annotations

import json
import logging
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any

from core.config import ALL_MODELS

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
    if not isinstance(profiles, dict) or set(profiles) != set(ALL_MODELS):
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


def annotate_catalog(catalog: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of a model catalog with registry availability and Ultra summaries."""
    profiles = ultra_profiles()
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
    return annotated
