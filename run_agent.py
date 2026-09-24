import json
import requests
import os
from git_mcp_tools import execute_tool as execute_git_tool, TOOL_REGISTRY as GIT_TOOLS
from filesystem_mcp_tools import execute_fs_tool, TOOL_REGISTRY as FS_TOOLS

API_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"

SYSTEM_PROMPT = """Ты — автономный инженерный агент Alice Pro в среде Termux (Android).
В твоем распоряжении инструменты Git (включая bare-репозитории) и файловой системы.

Если нужно вызвать инструмент, верни строго JSON вида:
{"tool": "имя_инструмента", "args": {"параметр": "значение"}}

Если задача выполнена или инструмент не требуется, верни:
{"text": "ответ пользователю"}
Всегда отвечай только валидным JSON без лишних префиксов."""

def dispatch_any_tool(tool_name: str, args: dict):
    if tool_name in GIT_TOOLS:
        return execute_git_tool(tool_name, args)
    if tool_name in FS_TOOLS:
        return execute_fs_tool(tool_name, args)
    return {"error": f"Инструмент '{tool_name}' не найден"}

def query_yandex(messages):
    payload = {
        "modelUri": f"gpt://{os.environ['YANDEX_PROJECT_ID']}/yandexgpt/latest",
        "completionOptions": {"temperature": 0.1, "maxTokens": "2000"},
        "messages": messages
    }
    resp = requests.post(
        API_URL,
        headers={"Authorization": f"Api-Key {os.environ['YANDEX_API_KEY']}", "Content-Type": "application/json"},
        json=payload,
        timeout=45
    )
    resp.raise_for_status()
    data = resp.json()
    return data["result"]["alternatives"][0]["message"]["text"]

def run_task(task_text: str, max_turns: int = 6):
    messages = [
        {"role": "system", "text": SYSTEM_PROMPT},
        {"role": "user", "text": task_text}
    ]

    print(f"🎯 Задача: {task_text}\n")

    for step in range(max_turns):
        raw_reply = query_yandex(messages)
        clean = raw_reply.strip()
        if clean.startswith("```"):
            clean = clean.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        try:
            decision = json.loads(clean)
        except Exception:
            return raw_reply

        if "tool" in decision:
            fn_name = decision["tool"]
            fn_args = decision.get("args", {})
            print(f"[Шаг {step+1}] Вызов инструмента: {fn_name}({fn_args})")
            
            result = dispatch_any_tool(fn_name, fn_args)
            print(f"  └ Результат: {json.dumps(result, ensure_ascii=False)[:250]}")

            messages.append({"role": "assistant", "text": json.dumps(decision, ensure_ascii=False)})
            messages.append({"role": "user", "text": f"Результат вызова {fn_name}:\n{json.dumps(result, ensure_ascii=False)}"})
        elif "text" in decision:
            return decision["text"]
        else:
            return clean

    return "Превышен лимит шагов выполнения."

if __name__ == "__main__":
    task = "Проверь статус /sdcard/repo/bare, привяжи его в текущем репозитории как remote 'local_bare' и покажи статус."
    res = run_task(task)
    print("\n🏁 Финальный ответ агента:")
    print(res)
