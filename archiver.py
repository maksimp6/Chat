import threading
from datetime import datetime, timedelta, timezone

import db as database


class DatabaseArchiver:
    """Delete chat messages older than 30 days from the application database."""

    def __init__(self):
        self._mutex = threading.Lock()
        self.schema_version = "1.2"

    def run_archive(self) -> bool:
        if not self._mutex.acquire(blocking=False):
            print("⚠️ Archival task already running. Skipping.")
            return False

        conn = database.get_conn()
        try:
            cursor = conn.cursor()
            # messages.created_at is a Unix timestamp in seconds (see db.add_message).
            cutoff = int((datetime.now(timezone.utc) - timedelta(days=30)).timestamp())

            cursor.execute(
                "SELECT id, content, created_at FROM messages WHERE created_at < ?",
                (cutoff,),
            )
            rows = cursor.fetchall()

            if not rows:
                return True

            cursor.execute("DELETE FROM messages WHERE created_at < ?", (cutoff,))
            conn.commit()
            print(
                f"💼 Successfully archived {len(rows)} records under schema v{self.schema_version}."
            )
            return True

        except Exception as exc:
            conn.rollback()
            print(f"❌ Transaction failed, rolling back: {exc}")
            return False
        finally:
            conn.close()
            self._mutex.release()
            print("🔓 Mutex released successfully.")
