"""Explicit maintenance of the same native journal used by Logos."""

from __future__ import annotations

import threading

from storage.host import get_memory_client


class DatabaseArchiver:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._mutex = threading.Lock()
        self.schema_version = "memory-v1"

    def run_archive(self) -> bool:
        if not self._mutex.acquire(blocking=False):
            return False
        try:
            # Compaction preserves all chat records; maintenance is not data deletion.
            get_memory_client(self.db_path).checkpoint()
            return True
        finally:
            self._mutex.release()
