"""Extract facts and persist individual records directly in native Kristall."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from typing import Any

import requests

from agent_memory.client import get_memory_client
from agent_memory.host import memory_file_path

API_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
_FACT_PREFIX = "global_fact:"
logger = logging.getLogger("logos.agent_memory.extractor")

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


def init_global_memory() -> None:
    """Open the native journal. There is no SQL schema to initialize."""
    get_memory_client(memory_file_path())


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
            logger.warning("skipping invalid native memory fact: %s", name)
            continue
        facts.append({"id": name[len(_FACT_PREFIX) :], **value})
    return sorted(facts, key=lambda row: (-row["updated_at"], row["id"]))


def save_global_memory_facts(
    facts: list[dict[str, Any]], *, now: int | None = None
) -> int:
    """Atomically insert facts; a repeated fact refreshes its category and time."""
    if not isinstance(facts, list):
        raise ValueError("facts must be a list")
    timestamp = int(time.time()) if now is None else int(now)
    saved = 0
    store = get_memory_client(memory_file_path())
    with store.transaction() as tx:
        for entry in facts:
            if not isinstance(entry, dict):
                continue
            fact = entry.get("fact")
            if not isinstance(fact, str):
                continue
            fact = fact.strip()
            if not fact or len(fact) > 8192:
                continue
            category = entry.get("category", "general")
            key = _FACT_PREFIX + hashlib.sha256(fact.encode("utf-8")).hexdigest()
            existing = tx.get(key)
            if not isinstance(category, str) or not category.strip():
                # A missing category never overwrites the one already stored.
                stored = (
                    existing.get("category") if isinstance(existing, dict) else None
                )
                category = (
                    stored if isinstance(stored, str) and stored.strip() else "general"
                )
            if existing is None:
                saved += 1
            tx.set(key, {"category": category, "fact": fact, "updated_at": timestamp})
    return saved


def extract_and_save_facts(dialog_messages: list) -> int:
    """Use the provider to extract stable facts, then commit only validated data."""
    if not dialog_messages:
        return 0
    try:
        formatted_chat = "\n".join(
            f"{msg['role']}: {msg['text']}" for msg in dialog_messages[-12:]
        )
        payload = {
            "modelUri": f"gpt://{os.environ['YANDEX_PROJECT_ID']}/yandexgpt/latest",
            "completionOptions": {
                "temperature": 0.1,
                "maxTokens": "1000",
                "responseFormat": {"type": "JSON_OBJECT", "jsonSchema": MEMORY_SCHEMA},
            },
            "messages": [
                {
                    "role": "system",
                    "text": "Извлеки устойчивые факты о проектах, путях и конфигурации.",
                },
                {"role": "user", "text": f"Факты из диалога:\n\n{formatted_chat}"},
            ],
        }
        response = requests.post(
            API_URL,
            headers={
                "Authorization": f"Api-Key {os.environ['YANDEX_API_KEY']}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=30,
        )
        if response.status_code != 200:
            logger.warning(
                "memory fact extraction: provider HTTP %s", response.status_code
            )
            return 0
        parsed = json.loads(
            response.json()["result"]["alternatives"][0]["message"]["text"]
        )
        if not isinstance(parsed, dict):
            logger.warning("memory fact extraction: response is not a JSON object")
            return 0
        facts = parsed.get("facts", [])
        if not isinstance(facts, list):
            logger.warning("memory fact extraction: facts is not a list")
            return 0
        return save_global_memory_facts(facts, now=int(time.time()))
    except (KeyError, ValueError, TypeError, requests.RequestException) as exc:
        logger.warning("memory fact extraction failed: %s", type(exc).__name__)
        return 0


def get_global_memory_summary() -> str:
    """Format current native memory facts for the system prompt."""
    records = sorted(
        list_global_memory_facts(), key=lambda row: (row["category"], row["fact"])
    )
    if not records:
        return ""
    return "\n[ГЛОБАЛЬНАЯ ПАМЯТЬ ПРОЕКТОВ]\n" + "".join(
        f"- [{row['category'].upper()}] {row['fact']}\n" for row in records
    )


if __name__ == "__main__":
    from composition import wiring

    wiring.register_all()
    print("Текущая глобальная память:")
    print(get_global_memory_summary() or "Память пока пуста.")
