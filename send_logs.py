import requests
import json
import os
from datetime import datetime


def collect_logs():
    logs = []
    if not os.path.exists("execution_logs"):
        print("Directory execution_logs/ not found")
        return logs
    for filename in os.listdir("execution_logs"):
        if filename.endswith(".json"):
            try:
                with open(f"execution_logs/{filename}", "r") as f:
                    logs.append(json.load(f))
            except Exception as e:
                print(f"Error reading {filename}: {e}")
    return logs


def send_logs():
    logs = collect_logs()
    if not logs:
        print("No logs to send")
        return
    data = {
        "session_id": f"sess_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        "user_id": "local_user",
        "logs": logs,
        "timestamp": int(datetime.now().timestamp()),
    }
    headers = {"Authorization": "Bearer YOUR_API_KEY", "Content-Type": "application/json"}
    try:
        response = requests.post(
            "https://api.yandex-ai-studio.ru/v1/logs", json=data, headers=headers, timeout=30
        )
        print(f"Status: {response.status_code}")
        print(f"Response: {response.text}")
    except requests.exceptions.RequestException as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    send_logs()
