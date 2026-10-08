"""Regression for snapshot frame delimiter in the experimental durable store."""

from durable_memory_db import DurableMemoryDatabase
from memory_db import Column


def test_snapshot_is_newline_delimited_and_reopens(tmp_path):
    path = tmp_path / "alice.memory"
    db = DurableMemoryDatabase(path)
    db.create_table("items", [Column("id", str, nullable=False, unique=True)])
    db.insert("items", id="saved")

    raw = path.read_bytes()
    assert raw.endswith(b"\n")
    assert b"\\n" not in raw[-2:]

    reopened = DurableMemoryDatabase(path)
    reopened.create_table("items", [Column("id", str, nullable=False, unique=True)])
    assert reopened.select("items") == [{"id": "saved"}]
