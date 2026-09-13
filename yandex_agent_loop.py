import os
import json
import time
import base64
import tempfile
import subprocess
import urllib.request
from agent_tools import dispatch_tool, TOOLS_SCHEMA
from yc_logging import yc_logger

FOLDER_ID = os.getenv("YC_FOLDER_ID", "b1g1fekh2198nuan1tnh")
IAM_KEY_PATH = os.getenv("YC_IAM_KEY", "iam_key.json")
MODEL_URI = f"gpt://{FOLDER_ID}/yandexgpt/latest"

def get_iam_token(key_path=IAM_KEY_PATH):
    """Генерация IAM-токена через подписание JWT с помощью openssl."""
    with open(key_path, "r", encoding="utf-8") as f:
        key_data = json.load(f)

    def b64url(b):
        return base64.urlsafe_b64encode(b).decode("utf-8").rstrip("=")

    header = {"alg": "PS256", "typ": "JWT", "kid": key_data["id"]}
    now = int(time.time())
    payload = {
        "iss": key_data["service_account_id"],
        "aud": "https://iam.api.cloud.yandex.net/iam/v1/tokens",
        "iat": now,
        "exp": now + 3600
    }

    h_b64 = b64url(json.dumps(header).encode("utf-8"))
    p_b64 = b64url(json.dumps(payload).encode("utf-8"))
    data_to_sign = f"{h_b64}.{p_b64}".encode("utf-8")

    with tempfile.NamedTemporaryFile("w", delete=False) as key_file:
        key_file.write(key_data["private_key"])
        key_file_name = key_file.name

    cmd = [
        "openssl", "dgst", "-sha256", "-sign", key_file_name,
        "-sigopt", "rsa_padding_mode:pss", "-sigopt", "rsa_pss_saltlen:-1"
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, _ = proc.communicate(data_to_sign)
    os.remove(key_file_name)

    jwt_token = f"{h_b64}.{p_b64}.{b64url(out)}"

    req = urllib.request.Request(
        "https://iam.api.cloud.yandex.net/iam/v1/tokens",
        data=json.dumps({"jwt": jwt_token}).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))["iamToken"]

def convert_tools_to_yandex(schemas):
    """Преобразование схемы agent_tools под спецификацию YandexGPT."""
    yandex_tools = []
    for s in schemas:
        yandex_tools.append({
            "function": {
                "name": s["name"],
                "description": s["description"],
                "parameters": s.get("parameters", {"type": "object", "properties": {}})
            }
        })
    return yandex_tools

def execute_yandex_turn(user_prompt: str, max_turns: int = 6):
    iam_token = get_iam_token()
    headers = {
        "Authorization": f"Bearer {iam_token}",
        "x-folder-id": FOLDER_ID,
        "Content-Type": "application/json"
    }

    messages = [
        {
            "role": "system",
            "text": "Ты автономный инженерный агент Termux. Управляй git-репозиториями (включая bare в /sdcard/repo/bare) и файлами через предоставленные инструменты."
        },
        {
            "role": "user",
            "text": user_prompt
        }
    ]

    tools_spec = convert_tools_to_yandex(TOOLS_SCHEMA)

    for turn in range(max_turns):
        payload = {
            "modelUri": MODEL_URI,
            "completionOptions": {
                "stream": False,
                "temperature": 0.2,
                "maxTokens": "2000"
            },
            "messages": messages,
            "tools": tools_spec
        }

        req = urllib.request.Request(
            "https://llm.api.cloud.yandex.net/foundationModels/v1/completion",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers
        )

        with urllib.request.urlopen(req, timeout=40) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        alternative = data["result"]["alternatives"][0]
        msg = alternative["message"]

        # Если модель выдала текстовый ответ без вызова инструментов
        if "toolCallList" not in msg:
            yc_logger.emit("INFO", "YandexGPT завершил диалог")
            return msg.get("text", "")

        # Добавляем ответ модели с вызовом функции в историю
        messages.append(msg)

        # Выполняем запрошенные инструменты
        tool_results = []
        for call in msg["toolCallList"]["toolCalls"]:
            fn_name = call["functionCall"]["name"]
            fn_args = call["functionCall"].get("arguments", {})

            yc_logger.emit("DEBUG", f"Yandex call: {fn_name}({fn_args})")
            exec_res = dispatch_tool(fn_name, fn_args)

            tool_results.append({
                "functionResult": {
                    "name": fn_name,
                    "content": json.dumps(exec_res, ensure_ascii=False)
                }
            })

        # Возвращаем результаты инструментов в следующем сообщении роли toolResultList
        messages.append({
            "role": "toolResultList",
            "toolResultList": {
                "toolResults": tool_results
            }
        })

    return "Превышен лимит итераций вызова инструментов."

if __name__ == "__main__":
    task = "Проверь статус репозитория /sdcard/repo/bare и выведи информацию о ветках."
    print("Запуск агента через YandexGPT API...")
    result = execute_yandex_turn(task)
    print("\n--- Ответ YandexGPT ---")
    print(result)
