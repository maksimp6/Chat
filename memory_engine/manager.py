"""Native MemoryStore configuration and fact controls."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from agent_memory.client import get_memory_client
from storage.db import memory_file_path
from memory_engine.extractor import list_global_memory_facts

_MEMORY_CONFIG_KEY = "global_memory_config"
DEFAULT_CONFIG = {
    "enabled": True,
    "max_context_facts": 15,
    "auto_extraction": True,
    "categories_allowed": ["git", "paths", "architecture", "tools", "general"],
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
        return value


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
        current.update(new_config)
        tx.set(_MEMORY_CONFIG_KEY, current)


def get_controlled_memory_summary() -> str:
    cfg = load_memory_config()
    if not cfg.get("enabled", True):
        return ""
    limit = cfg.get("max_context_facts", 15)
    if type(limit) is not int or limit < 0:
        raise ValueError("max_context_facts must be a nonnegative integer")
    allowed = cfg.get("categories_allowed", [])
    if not isinstance(allowed, list):
        raise ValueError("categories_allowed must be a list")
    facts = [
        item for item in list_global_memory_facts() if not allowed or item["category"] in allowed
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
            if not isinstance(record, dict):
                raise ValueError("invalid native memory fact")
            if category is None or record.get("category") == category:
                tx.delete(key)
