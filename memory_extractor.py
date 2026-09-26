import json
import sqlite3
import requests
import os
from db import get_conn, DB_PATH

API_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"

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
                        "description": "Категория факта (например: 'git', 'paths', 'architecture', 'tools')",
                    },
                    "fact": {
                        "type": "string",
                        "description": "Суть факта или договоренности в одном предложении",
                    },
                },
                "required": ["category", "fact"],
            },
        }
    },
    "required": ["facts"],
}


def init_global_memory():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS global_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT,
            fact TEXT NOT NULL UNIQUE,
            updated_at INTEGER NOT NULL
        )
    """)
    conn.commit()
    conn.close()


def extract_and_save_facts(dialog_messages: list):
    """Анализирует диалог, извлекает факты и сохраняет в глобальную память."""
    init_global_memory()

    if not dialog_messages:
        return 0

    formatted_chat = "\n".join([f"{msg['role']}: {msg['text']}" for msg in dialog_messages[-12:]])

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
                "text": "Ты аналитик памяти. Извлеки из диалога устойчивые факты о путях, окружении Termux, конфигурациях Git и техническом стеке, которые пригодятся в других сессиях.",
            },
            {"role": "user", "text": f"Проанализируй диалог и выдели факты:\n\n{formatted_chat}"},
        ],
    }

    try:
        resp = requests.post(
            API_URL,
            headers={
                "Authorization": f"Api-Key {os.environ['YANDEX_API_KEY']}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=30,
        )
        if resp.status_code != 200:
            return 0

        data = resp.json()
        raw_text = data["result"]["alternatives"][0]["message"]["text"]
        parsed = json.loads(raw_text)
        facts = parsed.get("facts", [])

        conn = get_conn()
        cur = conn.cursor()
        now = (
            int(requests.utils.datetime.datetime.utcnow().timestamp())
            if hasattr(requests, "utils")
            else 0
        )

        saved_count = 0
        for item in facts:
            cat = item.get("category", "general")
            fact_text = item.get("fact", "").strip()
            if not fact_text:
                continue
            try:
                cur.execute(
                    "INSERT OR IGNORE INTO global_memory (category, fact, updated_at) VALUES (?, ?, ?)",
                    (cat, fact_text, now),
                )
                if cur.rowcount > 0:
                    saved_count += 1
            except Exception:
                pass
        conn.commit()
        conn.close()
        return saved_count
    except Exception as e:
        print(f"❌ Ошибка извлечения фактов: {e}")
        return 0


def get_global_memory_summary() -> str:
    """Возвращает форматированный список всех глобальных фактов для system prompt."""
    init_global_memory()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT category, fact FROM global_memory ORDER BY category")
    rows = cur.fetchall()
    conn.close()

    if not rows:
        return ""

    summary = "\n[ГЛОБАЛЬНАЯ ПАМЯТЬ ПРОЕКТОВ]\n"
    for r in rows:
        summary += f"- [{r['category'].upper()}] {r['fact']}\n"
    return summary


if __name__ == "__main__":
    init_global_memory()
    print("Текущая глобальная память:")
    print(get_global_memory_summary() or "Память пока пуста.")
