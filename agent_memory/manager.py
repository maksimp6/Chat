"""Native Kristall configuration and fact controls."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from agent_memory.client import get_memory_client
from agent_memory.extractor import list_global_memory_facts
from agent_memory.host import memory_file_path

_MEMORY_CONFIG_KEY = "global_memory_config"
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
    # The most recent facts are chosen, then written in a fixed order: a block that
    # only changes when its facts change keeps the provider's prompt cache warm (#1742).
    facts.sort(key=lambda item: (item["category"], item["fact"]))
    return "\n[ГЛОБАЛЬНАЯ ПАМЯТЬ ПРОЕКТОВ]\n" + "".join(
        f"- [{item['category'].upper()}] {item['fact']}\n" for item in facts
    )
