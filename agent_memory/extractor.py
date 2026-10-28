"""Extract facts and persist individual records directly in native Kristall."""

from __future__ import annotations

from typing import Any


from agent_memory.client import get_memory_client
from storage.db import memory_file_path

API_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
_FACT_PREFIX = "global_fact:"

MEMORY_SCHEMA = {
    "type": "object",
    "properties": {
        "facts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "description": "Категория факта",
                    },
                    "fact": {
                        "type": "string",
                        "description": "Краткий устойчивый факт",
                    },
                },
                "required": ["category", "fact"],
            },
        }
    },
    "required": ["facts"],
}


def list_global_memory_facts() -> list[dict[str, Any]]:
    """Return detached facts, most recent first, with stable tie ordering."""
    raw = get_memory_client(memory_file_path()).items(prefix=_FACT_PREFIX)
    facts = []
    for name, value in raw.items():
        if (
            not isinstance(value, dict)
            or not isinstance(value.get("category"), str)
            or not isinstance(value.get("fact"), str)
            or type(value.get("updated_at")) is not int
        ):
            raise ValueError(f"invalid native memory fact: {name}")
        facts.append({"id": name[len(_FACT_PREFIX) :], **value})
    return sorted(facts, key=lambda row: (-row["updated_at"], row["id"]))


def get_global_memory_summary() -> str:
    """Format current native memory facts for the system prompt."""
    records = sorted(list_global_memory_facts(), key=lambda row: (row["category"], row["fact"]))
    if not records:
        return ""
    return "\n[ГЛОБАЛЬНАЯ ПАМЯТЬ ПРОЕКТОВ]\n" + "".join(
        f"- [{row['category'].upper()}] {row['fact']}\n" for row in records
    )


if __name__ == "__main__":
    print("Текущая глобальная память:")
    print(get_global_memory_summary() or "Память пока пуста.")
