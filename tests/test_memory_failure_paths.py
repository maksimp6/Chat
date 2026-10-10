"""Failure-path regressions for the public MemoryStore contract."""

import json

import pytest

from memory_engine import MemoryStore, StoreError


@pytest.mark.parametrize(
    "change",
    [
        None,
        {"namespace": 12, "key": "a", "op": "set", "value": 1},
        {"namespace": "values", "key": "a", "op": "invalid"},
    ],
)
def test_corrupt_legacy_change_is_rejected(tmp_path, change):
    import hashlib

    path = tmp_path / "corrupt.memory"
    payload = {"seq": 1, "changes": [change]}
    serialized = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    digest = hashlib.sha256(bytes.fromhex("0" * 64) + serialized).hexdigest()
    path.write_text(json.dumps({"payload": payload, "digest": digest}) + "\n")
    with pytest.raises(StoreError, match="corrupt committed journal"):
        MemoryStore(path)


def test_short_journal_write_fails_closed(tmp_path, monkeypatch):
    path = tmp_path / "short.memory"
    with MemoryStore(path) as store:
        store.set("a", 1)
        import memory_engine.store as module

        original_open = module.Path.open

        class ShortWriter:
            def __init__(self, stream):
                self.stream = stream

            def __enter__(self):
                self.stream.__enter__()
                return self

            def __exit__(self, *args):
                return self.stream.__exit__(*args)

            def write(self, data):
                return len(data) - 1

        def short_open(self, mode="r", *args, **kwargs):
            stream = original_open(self, mode, *args, **kwargs)
            if self == path and mode == "ab":
                return ShortWriter(stream)
            return stream

        monkeypatch.setattr(module.Path, "open", short_open)
        with pytest.raises(StoreError, match="commit failed"):
            store.commit()
        assert store.last_commit.sequence == 0
        with pytest.raises(StoreError, match="recovery required"):
            store.commit()


def test_invalid_later_change_does_not_mutate_committed_state():
    import hashlib
    import json
    from memory_engine.store import _JournalEngine

    state = {"values": {"original": 1}}
    payload = {"seq": 1, "changes": [
        {"namespace": "values", "key": "original", "op": "set", "value": 2},
        {"namespace": "values", "key": "bad", "op": "invalid"},
    ]}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    digest = hashlib.sha256(bytes.fromhex("0" * 64) + encoded).hexdigest()
    frame = json.dumps({"payload": payload, "digest": digest}).encode()
    with pytest.raises(StoreError, match="corrupt committed journal"):
        _JournalEngine._apply_verified_frame(frame, state, 0, "0" * 64)
    assert state == {"values": {"original": 1}}


def test_replay_does_not_deepcopy_accumulated_state(tmp_path, monkeypatch):
    import memory_engine.store as module

    path = tmp_path / "replay.memory"
    with MemoryStore(path) as store:
        for index in range(40):
            store.set(f"key-{index}", {"value": index})
            store.commit()

    def forbidden_copy(_value):
        raise AssertionError("journal replay must not deepcopy accumulated state")

    monkeypatch.setattr(module, "deepcopy", forbidden_copy)
    state, sequence, _digest = module._JournalEngine._inspect_log(path)
    assert sequence == 40
    assert state["values"]["key-39"] == {"value": 39}


def test_streaming_replay_never_reads_entire_journal(tmp_path, monkeypatch):
    from memory_engine.store import _JournalEngine
    path = tmp_path / "stream.memory"
    with MemoryStore(path) as store:
        for index in range(50):
            store.set(str(index), "v" * 128)
            store.commit()
    def forbidden_read(_path):
        raise AssertionError("full journal read is forbidden")
    monkeypatch.setattr(type(path), "read_bytes", forbidden_read)
    state, sequence, _ = _JournalEngine._inspect_log(path)
    assert sequence == 50 and state["values"]["49"] == "v" * 128


def test_streaming_replay_rejects_partial_tail_without_truncation(tmp_path):
    path = tmp_path / "partial.memory"
    with MemoryStore(path) as store:
        store.set("safe", "committed")
        store.commit()
    with path.open("ab") as stream:
        stream.write(b'{"incomplete":')
    original = path.read_bytes()
    with pytest.raises(StoreError, match="incomplete journal tail"):
        MemoryStore(path)
    assert path.read_bytes() == original


def test_streaming_replay_rejects_invalid_later_change_on_reopen(tmp_path):
    import hashlib
    import json
    path = tmp_path / "invalid.memory"
    with MemoryStore(path) as store:
        store.set("safe", "original")
        store.commit()
    payload = {"seq": 2, "changes": [
        {"namespace": "values", "key": "safe", "op": "set", "value": "changed"},
        {"namespace": "values", "key": "bad", "op": "unknown"},
    ]}
    # The frame is correctly hashed but semantically invalid.
    import memory_engine.store as module
    _, _, previous = module._JournalEngine._inspect_log(path)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    digest = hashlib.sha256(bytes.fromhex(previous) + encoded).hexdigest()
    with path.open("ab") as stream:
        stream.write(json.dumps({"payload": payload, "digest": digest}).encode() + b"\n")
    original = path.read_bytes()
    with pytest.raises(StoreError, match="corrupt committed journal"):
        MemoryStore(path)
    assert path.read_bytes() == original


def test_replay_rejects_oversized_frame_without_modifying_journal(tmp_path):
    from memory_engine.store import MAX_JOURNAL_FRAME_BYTES
    path = tmp_path / "oversize.memory"
    with MemoryStore(path) as store:
        store.set("safe", "ok")
        store.commit()
    with path.open("ab") as stream:
        stream.write(b"x" * (MAX_JOURNAL_FRAME_BYTES + 1))
    before = path.stat().st_size
    with pytest.raises(StoreError, match="journal frame exceeds size limit"):
        MemoryStore(path)
    assert path.stat().st_size == before


def test_commit_rejects_oversized_frame_without_publishing(tmp_path):
    from memory_engine.store import MAX_JOURNAL_FRAME_BYTES
    path = tmp_path / "too-large.memory"
    with MemoryStore(path) as store:
        store.set("large", "x" * MAX_JOURNAL_FRAME_BYTES)
        with pytest.raises(StoreError, match="journal frame exceeds size limit"):
            store.commit()
        assert store.last_commit.sequence == 0
        assert not path.exists()


def test_frame_limit_exact_boundary_and_one_byte_over(tmp_path, monkeypatch):
    import memory_engine.store as module
    monkeypatch.setattr(module, "MAX_JOURNAL_FRAME_BYTES", 256)
    with MemoryStore(tmp_path / "boundary.memory") as store:
        store.set("a", "")
        frame, _ = store._engine._encode_frame([
            {"op": "set", "namespace": "values", "key": "a", "value": ""}
        ])
        overhead = len(frame)
        exact = "x" * (256 - overhead)
        store.set("a", exact)
        assert store.commit() == 1
        store.set("a", exact + "x")
        with pytest.raises(StoreError, match="journal frame exceeds size limit"):
            store.commit()
        assert store.last_commit.sequence == 1


def test_rejects_deep_and_cyclic_values_before_staging(tmp_path):
    with MemoryStore(tmp_path / "shape.memory") as store:
        deep = "leaf"
        for _ in range(66):
            deep = [deep]
        with pytest.raises(StoreError, match="value structure exceeds limit"):
            store.set("deep", deep)
        cycle = []
        cycle.append(cycle)
        with pytest.raises(StoreError, match="shared or cyclic"):
            store.set("cycle", cycle)
        assert store.last_commit.sequence == 0
        store.set("valid", {"items": [1, 2, 3]})
        assert store.commit() == 1


def test_shared_references_and_json_type_contract(tmp_path):
    path = tmp_path / "types.memory"
    shared = [1, 2]
    with MemoryStore(path) as store:
        store.set("shared", {"left": shared, "right": shared})
        assert store.commit() == 1
        # Structural errors are rejected during staging; unsupported
        # scalar JSON values preserve the accepted commit-time contract.
        with pytest.raises(StoreError):
            store.set("bad", {1: "bad"})
        for bad in (b"bytes", float("nan")):
            store.set("bad", bad)
            with pytest.raises(StoreError):
                store.commit()
        store.set("bad", "valid")
        assert store.commit() == 2
        with store.transaction() as tx:
            tx.set("transaction_shared", {"a": shared, "b": shared})
            with pytest.raises(StoreError):
                tx.set("bad", {1: "bad"})
    with MemoryStore(path) as store:
        assert store.get("shared") == {"left": [1, 2], "right": [1, 2]}
        assert store.get("transaction_shared") == {"a": [1, 2], "b": [1, 2]}


def test_tuple_nested_cycle_is_rejected_before_copy(tmp_path):
    with MemoryStore(tmp_path / "tuple-cycle.memory") as store:
        outer = []
        nested = (outer,)
        outer.append(nested)
        with pytest.raises(StoreError, match="cyclic"):
            store.set("cycle", nested)
        assert store.last_commit.sequence == 0


def test_tuple_json_array_roundtrip_is_explicit(tmp_path):
    path = tmp_path / "tuple.memory"
    with MemoryStore(path) as store:
        store.set("tuple", (1, 2))
        assert store.get("tuple") == (1, 2)
        assert store.commit() == 1
    with MemoryStore(path) as store:
        assert store.get("tuple") == [1, 2]


def test_large_string_rejected_before_staging_and_existing_commit_preserved(tmp_path):
    from memory_engine.store import MAX_VALUE_TEXT_BYTES
    path = tmp_path / "text-limit.memory"
    with MemoryStore(path) as store:
        store.set("safe", "ok")
        assert store.commit() == 1
        with pytest.raises(StoreError, match="value text exceeds size limit"):
            store.set("oversize", "x" * (MAX_VALUE_TEXT_BYTES + 1))
        assert store.last_commit.sequence == 1
        assert store.get("oversize") is None
        store.set("safe", "still-ok")
        assert store.commit() == 2


def test_combined_text_and_unicode_limit(tmp_path, monkeypatch):
    import memory_engine.store as module
    monkeypatch.setattr(module, "MAX_VALUE_TEXT_BYTES", 10)
    with MemoryStore(tmp_path / "unicode.memory") as store:
        store.set("valid", ["é" * 5])
        with pytest.raises(StoreError, match="value text exceeds size limit"):
            store.set("bad", ["é" * 6])
        with pytest.raises(StoreError, match="value text exceeds size limit"):
            store.set("combined", ["abcd", "efgh", "ijk"])


def test_invalid_unicode_rejected_as_store_error(tmp_path):
    with MemoryStore(tmp_path / "unicode-error.memory") as store:
        for value in ("\ud800", {"\ud800": "ok"}, {"ok": "\udfff"}):
            with pytest.raises(StoreError, match="invalid Unicode text"):
                store.set("bad", value)
        assert store.last_commit.sequence == 0
        store.set("valid", "Привет 🌍")
        assert store.commit() == 1


def test_chunked_utf8_limit_across_boundaries(tmp_path, monkeypatch):
    import memory_engine.store as module
    monkeypatch.setattr(module, "MAX_VALUE_TEXT_BYTES", 32769)
    with MemoryStore(tmp_path / "chunked.memory") as store:
        store.set("valid", "a" * 32769)
        with pytest.raises(StoreError, match="value text exceeds size limit"):
            store.set("too_large", "a" * 32770)
        with pytest.raises(StoreError, match="value text exceeds size limit"):
            store.set("unicode", "Ж" * 16385)
        with pytest.raises(StoreError, match="invalid Unicode text"):
            store.set("surrogate", "a" * 16384 + "\ud800")


def test_dict_keys_count_toward_node_limit(tmp_path, monkeypatch):
    import memory_engine.store as module
    monkeypatch.setattr(module, "MAX_VALUE_NODES", 5)
    with MemoryStore(tmp_path / "keys.memory") as store:
        store.set("valid", {"a": 1, "b": 2})
        with pytest.raises(StoreError, match="value structure exceeds limit"):
            store.set("oversized", {"a": 1, "b": 2, "c": 3})


def test_large_json_string_chunking_keeps_commit_valid(tmp_path):
    path = tmp_path / "chunks.memory"
    with MemoryStore(path) as store:
        store.set("large", "Ж" * 40000)
        assert store.commit() == 1
    with MemoryStore(path) as store:
        assert store.get("large") == "Ж" * 40000
