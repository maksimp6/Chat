"""Offline archive contracts. No devices, network, credentials or publication."""

import base64
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from kristall import Kristall
from scripts.redmi_app_trace_snapshot import export_records, snapshot


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def record(self, trace):
        return {"id": "one", "created_at": 10, "status": "completed", "trace": trace}

    def test_inline_preserves_stored_content_and_excludes_unrelated_values(self):
        trace = {"prompt": "private fixture", "events": [1, 2]}
        manifest = export_records({"runtime_invocation:one": self.record(trace),
                                   "provider_credentials": {"fixture": "never export"}}, self.root)
        data = (self.root / manifest["records"][0]["path"]).read_bytes()
        self.assertEqual(json.loads(data)["trace"], trace)
        self.assertEqual(manifest["records"][0]["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(manifest["invocations"], 1)
        self.assertNotIn(b"never export", data)

    def test_chunk_integrity_is_required(self):
        data = b'{"events":["complete"]}'
        record = self.record(None)
        record["trace_chunks"] = {"format": "kristall-invocation-value-v1", "generation": "a" * 32,
                                  "chunks": 1, "bytes": len(data),
                                  "sha256": hashlib.sha256(data).hexdigest()}
        key = "runtime_invocation_value:one:trace:" + "a" * 32 + ":0"
        values = {"runtime_invocation:one": record,
                  key: {"data": base64.b64encode(data).decode()}}
        manifest = export_records(values, self.root)
        self.assertEqual(json.loads((self.root / manifest["records"][0]["path"]).read_text())["trace"],
                         json.loads(data))
        del values[key]
        with self.assertRaises(Exception):
            export_records(values, self.root)

    def test_empty_trace_is_counted(self):
        manifest = export_records({"runtime_invocation:one": self.record({})}, self.root)
        self.assertEqual(manifest["empty_traces"], 1)
        self.assertEqual(manifest["created_at_min"], 10)

    def test_native_snapshot_leaves_live_writer_and_sources_unchanged(self):
        memory = self.root / "memory"
        logs = self.root / "logs"
        logs.mkdir()
        (logs / "system.jsonl").write_bytes(b'{"message":"early failure"}\n')
        backend = self.root / "backend.log"
        backend.write_bytes(b"startup failure\n")
        with Kristall(memory) as store:
            with store.transaction() as tx:
                tx.set("runtime_invocation:one", self.record({"events": []}))
            before = memory.read_bytes()
            snapshot(memory, logs, backend, self.root / "out")
            self.assertEqual(memory.read_bytes(), before)
            self.assertEqual(store.get("runtime_invocation:one")["id"], "one")
        manifest = json.loads((self.root / "out/manifest.json").read_text())
        self.assertEqual(len(manifest["logs"]), 2)
        self.assertEqual(manifest["invocations"], 1)

    def test_source_change_fails_without_success_manifest(self):
        memory = self.root / "memory"
        with Kristall(memory):
            pass
        with patch("scripts.redmi_app_trace_snapshot.signature", side_effect=[(1,), (2,)]):
            with self.assertRaises(ValueError):
                snapshot(memory, self.root, self.root / "backend", self.root / "out")
        self.assertFalse((self.root / "out/manifest.json").exists())

    def test_existing_output_is_not_overwritten(self):
        with self.assertRaises(FileExistsError):
            snapshot(self.root / "memory", self.root, self.root / "backend", self.root)


if __name__ == "__main__":
    unittest.main()
