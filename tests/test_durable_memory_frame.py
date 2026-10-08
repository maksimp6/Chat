"""Regression for newline-framed synchronous Memory DB journal."""

from memory_engine import MemoryStore


def test_journal_frame_is_newline_delimited_and_reopens(tmp_path):
    path = tmp_path / "alice.memory"
    with MemoryStore(path) as db:
        db.set("items", "id", "saved")
    raw = path.read_bytes()
    assert raw.endswith(b"\n")
    assert raw.count(b"\n") == 1
    assert b"\\n" not in raw[-2:]
    with MemoryStore(path) as reopened:
        assert reopened.get("items", "id") == "saved"
