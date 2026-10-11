"""Fast standard-library smoke contracts for the standalone MemoryStore."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from memory_engine import MemoryStore, StoreError
import memory_engine.store as engine


class FastCoreContracts(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "db.memory"

    def test_explicit_commit_and_reopen(self):
        with MemoryStore(self.path) as db:
            db.set("draft", 1)
            self.assertEqual(db.last_commit.sequence, 0)
        with MemoryStore(self.path) as db:
            self.assertIsNone(db.get("draft"))
            db.set("saved", {"n": 1})
            self.assertEqual(db.commit(), 1)
        with MemoryStore(self.path) as db:
            self.assertEqual(db.get("saved"), {"n": 1})

    def test_atomic_batch_reopen(self):
        with MemoryStore(self.path) as db:
            db.set("a", 1)
            db.set("b", 2)
            self.assertEqual(db.commit(), 1)
        with MemoryStore(self.path) as db:
            self.assertEqual((db.get("a"), db.get("b")), (1, 2))
            self.assertEqual(db.last_commit.sequence, 1)

    def test_torn_tail_fails_closed_without_mutation(self):
        with MemoryStore(self.path) as db:
            db.set("safe", 1)
            db.commit()
        with self.path.open("ab") as stream:
            stream.write(b'{"partial":')
        before = self.path.read_bytes()
        with self.assertRaises(StoreError):
            MemoryStore(self.path)
        self.assertEqual(self.path.read_bytes(), before)

    def test_failed_fsync_does_not_acknowledge_commit(self):
        with MemoryStore(self.path) as db:
            db.set("unsafe", 1)
            before = db.last_commit
            with patch.object(engine.os, "fsync", side_effect=OSError("injected")):
                with self.assertRaises(StoreError):
                    db.commit()
            self.assertEqual(db.last_commit, before)
            with self.assertRaises(StoreError):
                db.commit()

    def test_second_writer_rejected(self):
        with MemoryStore(self.path):
            with self.assertRaises(StoreError):
                MemoryStore(self.path)


if __name__ == "__main__":
    unittest.main()
