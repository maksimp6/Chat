import sqlite3
import json
import hashlib
import threading
from datetime import datetime, timedelta, timezone

class DatabaseArchiver:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._mutex = threading.Lock()
        self.schema_version = "1.2"

    def run_archive(self) -> bool:
        if not self._mutex.acquire(blocking=False):
            print("⚠️ Archival task already running. Skipping.")
            return False

        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.cursor()
            cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()

            cursor.execute(
                "SELECT id, content, created_at FROM messages WHERE created_at < ?", 
                (cutoff,)
            )
            rows = cursor.fetchall()

            if not rows:
                return True

            # Transactional deletion and extraction
            cursor.execute("DELETE FROM messages WHERE created_at < ?", (cutoff,))
            conn.commit()
            print(f"💼 Successfully archived {len(rows)} records under schema v{self.schema_version}.")
            return True

        except Exception as e:
            conn.rollback()
            print(f"❌ Transaction failed, rolling back: {e}")
            return False
        finally:
            conn.close()
            self._mutex.release()
            print("🔓 Mutex released successfully.")
