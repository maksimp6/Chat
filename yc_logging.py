import json
import os
import sys
from datetime import datetime, timezone

LOG_FILE = "app_logs.jsonl"


class LocalLogger:
    def __init__(self, log_file=LOG_FILE):
        self.log_file = log_file

    def emit(self, level: str, message: str, stream_name: str = "alice_pro"):
        entry = {
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "level": level.upper(),
            "stream": stream_name,
            "message": str(message),
        }

        # Вывод в консоль Termux
        print(f"[{entry['timestamp']}] [{entry['level']}] {entry['message']}", flush=True)

        # Запись в локальный jsonl
        try:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def flush(self):
        sys.stdout.flush()


yc_logger = LocalLogger()
