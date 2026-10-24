"""Local atomic Screen DOM checkpoint and bounded revision journal."""

import json
import os
from pathlib import Path
import tempfile
import time


class StateStore:
    def __init__(self, root, limit=200):
        self.root = Path(root)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.snapshot = self.root / "state-checkpoint.json"
        self.journal = self.root / "state-revisions.jsonl"
        self.limit = limit

    def restore(self, merger):
        if not self.snapshot.exists():
            return False
        try:
            data = json.loads(self.snapshot.read_text())
            if data.get("version") != 1 or not isinstance(data.get("nodes"), dict):
                return False
            merger.nodes = data["nodes"]
            merger.revision = int(data["revision"])
            merger.next_id = int(data["next_id"])
            return True
        except (ValueError, KeyError, TypeError, OSError):
            return False

    def save(self, merger, events):
        # Local snapshot may contain OCR text: private permissions, never publish.
        data = {
            "version": 1,
            "revision": merger.revision,
            "next_id": merger.next_id,
            "nodes": merger.nodes,
        }
        fd, path = tempfile.mkstemp(prefix=".checkpoint-", dir=self.root)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(data, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(path, 0o600)
            os.replace(path, self.snapshot)
            directory = os.open(self.root, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(path):
                os.unlink(path)
        # Journal contains counts only, no OCR text or private screenshots.
        record = {
            "revision": merger.revision,
            "timestamp": time.time(),
            "appeared": sum(e["type"] == "element_appeared" for e in events),
            "disappeared": sum(e["type"] == "element_disappeared" for e in events),
        }
        rows = (
            self.journal.read_text().splitlines()[-(self.limit - 1) :]
            if self.journal.exists()
            else []
        )
        rows.append(json.dumps(record))
        fd, path = tempfile.mkstemp(prefix=".journal-", dir=self.root)
        try:
            with os.fdopen(fd, "w") as stream:
                stream.write("\n".join(rows) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(path, 0o600)
            os.replace(path, self.journal)
        finally:
            if os.path.exists(path):
                os.unlink(path)
