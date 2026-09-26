import json
import os
import requests
from agent_tools import dispatch_tool, TOOLS_SCHEMA
from yc_logging import yc_logger

# Конфигурация API модели (подходит любой OpenAI-совместимый эндпоинт)
API_URL = os.getenv("LLM_API_URL", "http://127.0.0.1:8000/v1/chat/completions")
API_KEY = os.getenv("LLM_API_KEY", "dummy-key")
MODEL_NAME = os.getenv("LLM_MODEL", "gpt-4o-mini")

SYSTEM_PROMPT = (
    "Ты — автономный агент-инженер в окружении Termux (Android). "
    "В твоем распоряжении инструменты для управления Git (включая bare-репозитории в /sdcard/repo/bare), "
    "выполнения shell-команд и точечной правки файлов. "
    "Всегда проверяй результаты выполнения команд перед финальным ответом."
)


def build_openai_tools(schemas):
    """Преобразует TOOLS_SCHEMA в формат OpenAI functions."""
    return [{"type": "function", "function": s} for s in schemas]


def execute_agent_turn(user_prompt: str, max_turns: int = 8):
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    tools = build_openai_tools(TOOLS_SCHEMA)

    yc_logger.emit("INFO", f"Запуск задачи агента: {user_prompt[:80]}")

    for step in range(max_turns):
        print(f"\n[Шаг {step + 1}] Отправка контекста в LLM...")

        payload = {"model": MODEL_NAME, "messages": messages, "tools": tools, "tool_choice": "auto"}

        try:
            resp = requests.post(
                API_URL,
                headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
                json=payload,
                timeout=45,
            )
            resp_data = resp.json()
        except Exception as e:
            yc_logger.emit("ERROR", f"Сбой обращения к LLM: {str(e)}")
            return f"Ошибка сетевого запроса к модели: {e}"

        if "choices" not in resp_data or not resp_data["choices"]:
            yc_logger.emit("ERROR", f"Некорректный ответ модели: {resp_data}")
            return f"Пустой или ошибочный ответ API: {resp_data}"

        choice = resp_data["choices"][0]["message"]
        tool_calls = choice.get("tool_calls")

        # Если модель не вызывает инструменты, возвращаем итоговый ответ
        if not tool_calls:
            yc_logger.emit("INFO", "Модель завершила задачу и выдала финальный ответ.")
            return choice.get("content", "")

        # Добавляем намерение модели в контекст
        messages.append(choice)

        # Выполняем запрошенные инструменты
        for call in tool_calls:
            func_name = call["function"]["name"]
            try:
                args = json.loads(call["function"].get("arguments", "{}"))
            except Exception:
                args = {}

            call_id = call.get("id", "call_default")
            print(f"-> Исполнение инструмента: {func_name}({args})")
            yc_logger.emit("DEBUG", f"Call {func_name} with {args}")

            output = dispatch_tool(func_name, args)

            # Передаем результат выполнения обратно в историю сообщений
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": json.dumps(output, ensure_ascii=False),
                }
            )

    return "Превышен лимит итераций (max_turns) без завершения задачи."


if __name__ == "__main__":
    task = "Проверь статус /sdcard/repo/bare и покажи последние коммиты."
    result = execute_agent_turn(task)
    print("\n=== Финальный ответ агента ===")
    print(result)
