from memory_extractor import get_global_memory_summary
from db import get_messages


def build_prompt_with_memory(conversation_id: str, system_instructions: str = None) -> list:
    """Собирает системный промпт с глобальной памятью и последние сообщения диалога."""
    base_system = system_instructions or "Ты — автономный инженерный агент Alice Pro в Termux."
    global_memory = get_global_memory_summary()

    # Объединяем системные инструкции и глобальную базу фактов
    full_system = f"{base_system}\n{global_memory}"

    llm_messages = [{"role": "system", "text": full_system}]

    # Подтягиваем историю конкретного диалога из SQLite
    history = get_messages(conversation_id)
    for msg in history[-10:]:  # Берем последние 10 сообщений для экономии токенов
        role = "assistant" if msg["role"] in ("assistant", "ai") else "user"
        llm_messages.append({"role": role, "text": msg["text"]})

    return llm_messages
