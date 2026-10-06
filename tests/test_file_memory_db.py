import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from agent_memory.file_memory_db import FileMemoryCorruption, FileMemoryDB


def test_append_recover_and_delete(tmp_path):
    path = tmp_path / "alice.memory"
    db = FileMemoryDB(path)
    assert db.put("a", {"value": 1}) == 1
    assert db.put("b", "two") == 2
    assert db.delete("a") == 3

    restored = FileMemoryDB(path)
    assert restored.sequence == 3
    assert restored.items() == {"b": "two"}
    assert restored.get("b") == "two"
    assert restored.get("missing", "fallback") == "fallback"


def test_truncated_tail_is_discarded(tmp_path):
    path = tmp_path / "alice.memory"
    db = FileMemoryDB(path)
    db.put("a", 1)
    with path.open("ab") as handle:
        handle.write(b'{"op":"put","seq":2')

    restored = FileMemoryDB(path)
    assert restored.sequence == 1
    assert restored.items() == {"a": 1}
    assert path.read_bytes().endswith(b"\n")


def test_corruption_in_committed_middle_is_fatal(tmp_path):
    path = tmp_path / "alice.memory"
    db = FileMemoryDB(path)
    db.put("a", 1)
    db.put("b", 2)
    lines = path.read_bytes().splitlines(keepends=True)
    first = json.loads(lines[0])
    first["value"] = 999
    lines[0] = (json.dumps(first, separators=(",", ":"), sort_keys=True) + "\n").encode()
    path.write_bytes(b"".join(lines))

    with pytest.raises(FileMemoryCorruption):
        FileMemoryDB(path)


def test_compaction_keeps_one_authoritative_file(tmp_path):
    path = tmp_path / "alice.memory"
    db = FileMemoryDB(path)
    db.put("a", 1)
    db.put("b", 2)
    db.put("a", 3)
    db.delete("b")
    before = db.sequence

    db.compact()

    assert path.exists()
    assert not path.with_name("alice.memory.tmp").exists()
    assert len(path.read_text().splitlines()) == 1
    restored = FileMemoryDB(path)
    assert restored.sequence == before
    assert restored.items() == {"a": 3}


def test_concurrent_writers_get_monotonic_records(tmp_path):
    path = tmp_path / "alice.memory"
    db = FileMemoryDB(path)

    def write(index):
        return db.put(f"k{index}", index)

    with ThreadPoolExecutor(max_workers=8) as pool:
        sequences = list(pool.map(write, range(50)))

    assert sorted(sequences) == list(range(1, 51))
    restored = FileMemoryDB(path)
    assert restored.sequence == 50
    assert len(restored.items()) == 50


def test_fsync_failure_does_not_publish_memory_state(tmp_path, monkeypatch):
    path = tmp_path / "alice.memory"
    db = FileMemoryDB(path)

    def fail(_fd):
        raise OSError("disk unavailable")

    monkeypatch.setattr("agent_memory.file_memory_db.os.fsync", fail)
    with pytest.raises(OSError):
        db.put("a", 1)

    assert db.sequence == 0
    assert db.items() == {}


def test_invalid_keys_are_rejected(tmp_path):
    db = FileMemoryDB(tmp_path / "alice.memory")
    with pytest.raises(ValueError, match="non-empty"):
        db.put("", 1)
    with pytest.raises(ValueError, match="non-empty"):
        db.delete("")


def test_short_write_is_an_error(monkeypatch):
    monkeypatch.setattr("agent_memory.file_memory_db.os.write", lambda _fd, _payload: 0)
    with pytest.raises(OSError, match="short write"):
        FileMemoryDB._write_all(1, b"x")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda record: record.update(v=99),
        lambda record: record.update(seq=-1),
        lambda record: record.update(op="noop"),
        lambda record: record.update(key=123),
        lambda record: record.update(sha256="bad"),
    ],
)
def test_invalid_complete_last_record_is_corruption(tmp_path, mutate):
    path = tmp_path / "alice.memory"
    db = FileMemoryDB(path)
    db.put("a", 1)
    record = json.loads(path.read_text().splitlines()[0])
    mutate(record)
    corrupted = (json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n").encode()
    path.write_bytes(corrupted)

    with pytest.raises(FileMemoryCorruption, match="corrupt record"):
        FileMemoryDB(path)

    assert path.read_bytes() == corrupted


def test_non_monotonic_committed_record_is_corruption(tmp_path):
    path = tmp_path / "alice.memory"
    db = FileMemoryDB(path)
    db.put("a", 1)
    first = path.read_bytes()
    path.write_bytes(first + first)

    with pytest.raises(FileMemoryCorruption, match="corrupt record"):
        FileMemoryDB(path)


def test_invalid_snapshot_at_tail_is_corruption(tmp_path):
    path = tmp_path / "alice.memory"
    bad = {"v": 1, "seq": 0, "op": "snapshot", "value": []}
    bad["sha256"] = FileMemoryDB._checksum({k: v for k, v in bad.items() if k != "sha256"})
    corrupted = (json.dumps(bad, separators=(",", ":"), sort_keys=True) + "\n").encode()
    path.write_bytes(corrupted)

    with pytest.raises(FileMemoryCorruption, match="corrupt record"):
        FileMemoryDB(path)

    assert path.read_bytes() == corrupted


def test_parent_fsync_failure_is_reported(tmp_path, monkeypatch):
    path = tmp_path / "alice.memory"
    db = FileMemoryDB(path)
    calls = 0

    real_fsync = __import__("os").fsync

    def fail_second(fd):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("parent fsync failed")
        return real_fsync(fd)

    monkeypatch.setattr("agent_memory.file_memory_db.os.fsync", fail_second)
    with pytest.raises(OSError, match="parent fsync failed"):
        db.put("a", 1)
