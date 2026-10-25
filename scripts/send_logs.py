import requests
import json
import os
import re
import uuid
from datetime import datetime
from pathlib import Path

LOGS_DIR = Path(__file__).resolve().parents[1] / "execution_logs"
# Whole-word credential names only: "input_tokens" is usage data and must still be sent.
_SENSITIVE_KEY_RE = re.compile(
    r"api[_-]?key|authorization|password|passwd|secret|cookie|private[_-]?key|credential"
    r"|(?:^|[_-])token(?:$|[_-])",
    re.IGNORECASE,
)


def _redact(value):
    # Logs leave the machine, so credential-bearing fields are removed before upload.
    if isinstance(value, dict):
        return {
            str(key): _redact(item)
            for key, item in value.items()
            if not _SENSITIVE_KEY_RE.search(str(key))
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def collect_logs():
    logs = []
    if not LOGS_DIR.is_dir():
        print("Directory execution_logs/ not found")
        return logs
    for filename in os.listdir(LOGS_DIR):
        if filename.endswith(".json"):
            try:
                with open(LOGS_DIR / filename, encoding="utf-8") as f:
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
        # A random suffix keeps two uploads in the same second from sharing a session id.
        "session_id": f"sess_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}",
        "user_id": os.environ.get("LOGOS_USER_ID", "").strip() or "local_user",
        "logs": _redact(logs),
        "timestamp": int(datetime.now().timestamp()),
    }
    api_key = os.environ.get("YANDEX_API_KEY", "").strip()
    if not api_key:
        print("YANDEX_API_KEY is not set; nothing was sent")
        return
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    try:
        response = requests.post(
            "https://api.yandex-ai-studio.ru/v1/logs", json=data, headers=headers, timeout=30
        )
        print(f"Status: {response.status_code}")
        if response.status_code >= 400:
            # The body is shown only for a refusal; a success needs no echo of what was sent.
            print(f"Response: {response.text[:500]}")
            return 1
    except requests.exceptions.RequestException as e:
        print(f"Error: {e}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(send_logs())
