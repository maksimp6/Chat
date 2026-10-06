import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from runtime.file_memory_db import FileMemoryCorruption, FileMemoryDB


def test_append_recover_and_delete(tmp_path):
    path = tmp_path / "alice.memory"
    db = FileMemoryDB(path)
    assert db.put("a", {"value": 1}) == 1
    assert db.put("b", "two") == 2
    assert db.delete("a") == 3

    restored = FileMemoryDB(path)
    assert restored.sequence == 3
    assert restored.items() == {"b": "two"}


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

    monkeypatch.setattr("runtime.file_memory_db.os.fsync", fail)
    with pytest.raises(OSError):
        db.put("a", 1)

    assert db.sequence == 0
    assert db.items() == {}
