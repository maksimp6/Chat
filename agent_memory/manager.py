"""Native Kristall configuration and fact controls."""

from __future__ import annotations

import logging
from copy import deepcopy
from typing import Any

from agent_memory.client import get_memory_client
from agent_memory.extractor import list_global_memory_facts
from agent_memory.host import memory_file_path

_MEMORY_CONFIG_KEY = "global_memory_config"
logger = logging.getLogger("logos.agent_memory.manager")
# Settings at maximum: the most facts the validator accepts, every category (an absent
# categories_allowed key means all), facts extracted after every completed turn.
# settings_revision marks records written with these defaults; older ones are upgraded.
MEMORY_SETTINGS_REVISION = 2
DEFAULT_CONFIG = {
    "enabled": True,
    "max_context_facts": 100,
    "auto_extraction": True,
    "settings_revision": MEMORY_SETTINGS_REVISION,
}


def load_memory_config() -> dict[str, Any]:
    store = get_memory_client(memory_file_path())
    with store.transaction() as tx:
        value = tx.get(_MEMORY_CONFIG_KEY)
        if value is None:
            value = deepcopy(DEFAULT_CONFIG)
            tx.set(_MEMORY_CONFIG_KEY, value)
        if not isinstance(value, dict):
            raise ValueError("invalid native memory configuration")
        revision = value.get("settings_revision")
        if not (isinstance(revision, int) and not isinstance(revision, bool)) or (
            revision < MEMORY_SETTINGS_REVISION
        ):
            # Records saved before the maximal defaults held 15 facts, a category
            # list and extraction that never ran. They move to the maximum once.
            value = {
                **value,
                "max_context_facts": MAX_CONTEXT_FACTS_LIMIT,
                "auto_extraction": True,
                "settings_revision": MEMORY_SETTINGS_REVISION,
            }
            value.pop("categories_allowed", None)
            tx.set(_MEMORY_CONFIG_KEY, value)
        return value


MAX_CONTEXT_FACTS_LIMIT = 100
_CONFIG_TYPES = {"enabled": bool, "auto_extraction": bool, "categories_allowed": list}


def _validated_config_update(new_config: dict) -> dict:
    """Accept only known keys with the types the summary builder relies on."""
    unknown = set(new_config) - set(_CONFIG_TYPES) - {"max_context_facts"}
    if unknown:
        raise ValueError("unknown memory config keys: " + ", ".join(sorted(unknown)))
    clean: dict[str, Any] = {}
    for key, value in new_config.items():
        if key == "max_context_facts":
            if type(value) is not int or not 0 <= value <= MAX_CONTEXT_FACTS_LIMIT:
                raise ValueError(
                    f"max_context_facts must be an integer from 0 to {MAX_CONTEXT_FACTS_LIMIT}"
                )
        elif key == "categories_allowed":
            if not isinstance(value, list) or not all(
                isinstance(item, str) and item.strip() for item in value
            ):
                raise ValueError(
                    "categories_allowed must be a list of non-empty strings"
                )
        elif type(value) is not _CONFIG_TYPES[key]:
            raise ValueError(f"{key} must be a {_CONFIG_TYPES[key].__name__}")
        clean[key] = value
    return clean


def save_memory_config(new_config: dict) -> None:
    if not isinstance(new_config, dict):
        raise ValueError("memory config must be an object")
    store = get_memory_client(memory_file_path())
    with store.transaction() as tx:
        current = tx.get(_MEMORY_CONFIG_KEY)
        if current is None:
            current = deepcopy(DEFAULT_CONFIG)
        if not isinstance(current, dict):
            raise ValueError("invalid native memory configuration")
        current.update(_validated_config_update(new_config))
        tx.set(_MEMORY_CONFIG_KEY, current)


def get_controlled_memory_summary() -> str:
    cfg = load_memory_config()
    if not cfg.get("enabled", True):
        return ""
    limit = cfg.get("max_context_facts", MAX_CONTEXT_FACTS_LIMIT)
    if type(limit) is not int or limit < 0:
        raise ValueError("max_context_facts must be a nonnegative integer")
    # An absent key keeps every category; an empty list allows none.
    restricted = "categories_allowed" in cfg
    allowed = cfg.get("categories_allowed", [])
    if not isinstance(allowed, list):
        raise ValueError("categories_allowed must be a list")
    facts = [
        item
        for item in list_global_memory_facts()
        if not restricted or item["category"] in allowed
    ][:limit]
    if not facts:
        return ""
    return "\n[ГЛОБАЛЬНАЯ ПАМЯТЬ ПРОЕКТОВ]\n" + "".join(
        f"- [{item['category'].upper()}] {item['fact']}\n" for item in facts
    )


def clear_global_memory(category: str | None = None) -> None:
    """Remove matching facts in one journal transaction; preserve config."""
    store = get_memory_client(memory_file_path())
    with store.transaction() as tx:
        for key, record in store.items(prefix="global_fact:").items():
            if not isinstance(record, dict) and category is not None:
                logger.warning("skipping invalid native memory fact: %s", key)
                continue
            if category is None or record.get("category") == category:
                tx.delete(key)
