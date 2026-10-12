"""Bounded JSON shapes cannot corrupt staging or bypass replay validation."""

import hashlib
import json

import pytest

from memory_engine import MemoryStore, StoreError


def nested(depth):
    value = "leaf"
    for _ in range(depth):
        value = [value]
    return value


@pytest.mark.parametrize("mode", ["staging", "transaction"])
@pytest.mark.parametrize("shape", ["deep", "cycle", "tuple_cycle"])
def test_invalid_shape_is_rejected_before_copy_and_writer_remains_usable(tmp_path, mode, shape):
    path = tmp_path / "values.memory"
    value = nested(66)
    if shape != "deep":
        value = []
        value.append(value if shape == "cycle" else (value,))
    with MemoryStore(path) as store:
        store.set("saved", 1)
        store.commit()
        before = path.read_bytes()
        if mode == "staging":
            with pytest.raises(StoreError, match="structure|cyclic"):
                store.set("bad", value)
            assert store.get("bad") is None
        else:
            with store.transaction() as tx:
                with pytest.raises(StoreError, match="structure|cyclic"):
                    tx.set("bad", value)
                assert tx.get("bad") is None
        assert path.read_bytes() == before
        assert store.info().last_commit == 1
        store.set("next", 2)
        assert store.commit() == 2
    with MemoryStore(path) as reopened:
        assert reopened.get("saved") == 1
        assert reopened.get("next") == 2


def test_depth_boundary_and_shared_values_round_trip(tmp_path):
    shared = [1, 2]
    path = tmp_path / "boundary.memory"
    with MemoryStore(path) as store:
        store.set("limit", nested(64))
        with pytest.raises(StoreError, match="structure"):
            store.set("over", nested(65))
        store.set("shared", {"a": shared, "b": shared})
        store.commit()
        store.checkpoint()
    with MemoryStore(path) as reopened:
        assert reopened.get("limit") == nested(64)
        assert reopened.get("shared") == {"a": [1, 2], "b": [1, 2]}


def test_dictionary_keys_count_toward_node_budget(tmp_path, monkeypatch):
    import memory_engine.store as module

    monkeypatch.setattr(module, "MAX_VALUE_NODES", 5)
    with MemoryStore(tmp_path / "nodes.memory") as store:
        store.set("limit", {"a": 1, "b": 2})
        with pytest.raises(StoreError, match="structure"):
            store.set("over", {"a": 1, "b": 2, "c": 3})
        assert store.get("over") is None
        assert store.commit() == 1


@pytest.mark.parametrize("depth", [66, 1100])
def test_correct_digest_does_not_bypass_depth_limit_on_replay(tmp_path, depth):
    value = "[" * depth + '"leaf"' + "]" * depth
    # Construct canonical JSON without asking Python's encoder to recurse.
    payload = '{"changes":[{"key":"bad","namespace":"values","op":"set","value":' + value
    payload += '}],"format":1,"seq":1}'
    digest = hashlib.sha256(bytes(32) + payload.encode()).hexdigest()
    frame = '{"digest":' + json.dumps(digest) + ',"payload":' + payload + "}\n"
    path = tmp_path / "invalid.memory"
    path.write_text(frame)
    before = path.read_bytes()
    with pytest.raises(StoreError, match="corrupt committed journal"):
        MemoryStore(path)
    assert path.read_bytes() == before


def test_unsupported_json_types_still_fail_at_commit(tmp_path):
    with MemoryStore(tmp_path / "types.memory") as store:
        for value in ((1, 2), {1: "value"}, float("nan")):
            store.set("value", value)
            with pytest.raises(StoreError, match="unsupported JSON"):
                store.commit()
        store.set("value", "corrected")
        assert store.commit() == 1
