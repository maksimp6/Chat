import os
import sys
import json
import subprocess
import requests

API_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"

SYSTEM_PROMPT = """Ты — автономный CLI-ассистент внутри Termux на Android.
Твоя цель: помогать пользователю решать задачи в консоли.
Когда нужно выполнить команду в терминале, ответь строго в формате JSON:
{"command": "команда_для_termux", "explanation": "зачем это нужно"}

Если команда не нужна и ты просто отвечаешь, верни:
{"text": "твой ответ пользователю"}
Всегда возвращай ТОЛЬКО валидный JSON."""

history = [{"role": "system", "text": SYSTEM_PROMPT}]

def query_llm(messages):
    payload = {
        "modelUri": f"gpt://{os.environ['YANDEX_PROJECT_ID']}/yandexgpt/latest",
        "completionOptions": {"temperature": 0.2, "maxTokens": "2000"},
        "messages": messages
    }
    resp = requests.post(
        API_URL,
        headers={"Authorization": f"Api-Key {os.environ['YANDEX_API_KEY']}", "Content-Type": "application/json"},
        json=payload,
        timeout=60
    )
    resp.raise_for_status()
    data = resp.json()
    return data["result"]["alternatives"][0]["message"]["text"]

def run_shell(cmd):
    print(f"\n⚡ Выполняю: {cmd}")
    res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    out = res.stdout.strip()
    err = res.stderr.strip()
    returncode = res.returncode
    
    result_str = f"EXIT_CODE: {returncode}\n"
    if out:
        result_str += f"STDOUT:\n{out}\n"
    if err:
        result_str += f"STDERR:\n{err}\n"
    if not out and not err:
        result_str += "(нет вывода)\n"
    return result_str

print("🚀 Alice Pro CLI Agent активен. Введите запрос (или 'exit' для выхода):\n")

while True:
    try:
        user_input = input("User > ").strip()
    except (KeyboardInterrupt, EOFError):
        break
        
    if not user_input or user_input.lower() in ("exit", "quit"):
        break

    history.append({"role": "user", "text": user_input})
    
    # Цикл выполнения команд и анализа вывода
    while True:
        try:
            raw_reply = query_llm(history)
            # Очистка разметки markdown, если модель обернула в ```json
            clean = raw_reply.strip()
            if clean.startswith("```"):
                clean = clean.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            action = json.loads(clean)
        except Exception:
            print(f"\nAI > {raw_reply}\n")
            history.append({"role": "assistant", "text": raw_reply})
            break

        if "command" in action:
            cmd = action["command"]
            if action.get("explanation"):
                print(f"ℹ️  {action['explanation']}")
            
            output = run_shell(cmd)
            print(output)
            
            history.append({"role": "assistant", "text": raw_reply})
            history.append({"role": "user", "text": f"Вывод команды:\n{output}"})
        elif "text" in action:
            print(f"\nAI > {action['text']}\n")
            history.append({"role": "assistant", "text": action["text"]})
            break
        else:
            print(f"\nAI > {raw_reply}\n")
            break
