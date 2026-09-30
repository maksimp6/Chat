import json
import sqlite3
import uuid

import pytest

from agent_memory import AgentMemoryStore, MemoryLookup, MemoryRecord


def _sqlite_store(tmp_path):
    path = tmp_path / "agent-memory.db"

    def connect():
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        return conn

    return AgentMemoryStore(connect)


def _github_record(
    *,
    memory_id="memory-1",
    kind="project",
    scope="repo:maksimp6/Chat",
    source_version="sha-1",
    now=100,
    expires_at=None,
    visibility=("backend-engineer", "team-lead"),
):
    return MemoryRecord.create(
        memory_id=memory_id,
        kind=kind,
        scope=scope,
        text="SQLite remains the local default.",
        provenance={
            "source_type": "github",
            "repository": "maksimp6/Chat",
            "refs": ["PR#582", "docs/architecture/agent-context-cache.md"],
        },
        source_version=source_version,
        payload={"facts": [{"name": "database", "value": "sqlite"}]},
        confidence=0.95,
        visibility=visibility,
        expires_at=expires_at,
        now=now,
    )


def test_memory_record_requires_supported_contract():
    base = {
        "memory_id": "m1",
        "kind": "project",
        "scope": "repo",
        "text": "fact",
        "provenance": {"source_type": "github", "repository": "repo", "refs": ["PR#1"]},
        "source_version": "sha",
        "created_at": 1,
        "updated_at": 1,
    }
    for field in ("memory_id", "kind", "scope", "text", "source_version"):
        values = dict(base)
        values[field] = ""
        with pytest.raises(ValueError):
            MemoryRecord(**values)

    with pytest.raises(ValueError, match="unsupported memory kind"):
        MemoryRecord(**{**base, "kind": "guess"})
    with pytest.raises(ValueError, match="unsupported memory status"):
        MemoryRecord(**{**base, "status": "deleted"})
    with pytest.raises(ValueError, match="unsupported sensitivity"):
        MemoryRecord(**{**base, "sensitivity": "secret"})
    with pytest.raises(ValueError, match="confidence"):
        MemoryRecord(**{**base, "confidence": 1.5})
    with pytest.raises(ValueError, match="freshness"):
        MemoryRecord(**{**base, "freshness": {}})


def test_memory_record_requires_provenance_and_github_repository():
    common = dict(
        memory_id="m1",
        kind="project",
        scope="repo",
        text="fact",
        source_version="sha",
        created_at=1,
        updated_at=1,
    )
    with pytest.raises(ValueError, match="source_type"):
        MemoryRecord(**common, provenance={})
    with pytest.raises(ValueError, match="repository"):
        MemoryRecord(
            **common,
            provenance={"source_type": "github", "refs": ["PR#1"]},
        )


def test_memory_record_sanitizes_and_deep_freezes_payload():
    record = MemoryRecord.create(
        memory_id="m1",
        kind="task",
        scope="issue:578",
        text="authorization: Bearer supersecret",
        provenance={
            "source_type": "github",
            "repository": "maksimp6/Chat",
            "refs": ["issue#578"],
        },
        source_version="sha",
        payload={
            "access_token": "supersecret",
            "nested": {"items": [{"value": "safe"}]},
        },
        now=10,
    )

    exported = record.as_dict()
    assert "supersecret" not in json.dumps(exported)
    exported["payload"]["nested"]["items"][0]["value"] = "changed"
    assert record.as_dict()["payload"]["nested"]["items"][0]["value"] == "safe"

    with pytest.raises(TypeError):
        record.payload["nested"]["items"][0]["value"] = "direct-change"


def test_expiration_and_lookup_status_validation():
    record = _github_record(expires_at=110)
    assert record.is_expired(now=109) is False
    assert record.is_expired(now=110) is True
    with pytest.raises(ValueError):
        MemoryLookup(status="maybe", memory_id="m1")


def test_sqlite_round_trip_freshness_and_visibility(tmp_path):
    store = _sqlite_store(tmp_path)
    record = _github_record()
    stored = store.upsert(record)

    assert stored.memory_id == record.memory_id
    assert store.lookup("memory-1", source_version="sha-1", now=101).status == "hit"
    changed = store.lookup("memory-1", source_version="sha-2", now=101)
    assert changed.status == "stale"
    assert changed.reason == "source_version_changed"
    assert store.lookup("missing").status == "miss"

    assert [item.memory_id for item in store.list_records(kind="project")] == ["memory-1"]
    assert store.list_records(visible_to="backend-engineer")
    assert store.list_records(visible_to="frontend-engineer") == []


def test_upsert_preserves_created_at_and_updates_content(tmp_path):
    store = _sqlite_store(tmp_path)
    first = _github_record(now=100)
    store.upsert(first)
    second = MemoryRecord(
        **{
            **first.as_dict(),
            "text": "Updated accepted project fact.",
            "updated_at": 200,
            "created_at": 999,
        }
    )
    stored = store.upsert(second)

    assert stored.text == "Updated accepted project fact."
    assert stored.created_at == 100
    assert stored.updated_at == 200


def test_mark_stale_and_supersede_require_existing_records(tmp_path):
    store = _sqlite_store(tmp_path)
    first = _github_record(memory_id="old")
    replacement = _github_record(memory_id="new", source_version="sha-2")
    store.upsert(first)
    store.upsert(replacement)

    assert store.mark_stale("missing", reason="nope", now=150) is False
    assert store.mark_stale("old", reason="master_changed", now=150) is True
    stale = store.lookup("old", source_version="sha-1", now=150)
    assert stale.status == "stale"
    assert stale.reason == "marked_stale"
    assert stale.record.as_dict()["freshness"]["stale_reason"] == "master_changed"

    third = _github_record(memory_id="third")
    store.upsert(third)
    with pytest.raises(ValueError, match="itself"):
        store.supersede("third", replacement_id="third")
    with pytest.raises(ValueError, match="replacement"):
        store.supersede("third", replacement_id="missing")
    assert store.supersede("third", replacement_id="new", now=160) is True
    superseded = store.lookup("third")
    assert superseded.status == "superseded"
    assert superseded.record.superseded_by == "new"
    assert store.supersede("missing", replacement_id="new") is False


def test_expired_record_is_stale_and_export_is_stable(tmp_path):
    store = _sqlite_store(tmp_path)
    store.upsert(_github_record(memory_id="b", expires_at=105))
    store.upsert(_github_record(memory_id="a", source_version="sha-2"))

    expired = store.lookup("b", source_version="sha-1", now=105)
    assert expired.status == "stale"
    assert expired.reason == "expired"

    exported = store.export_records()
    assert [item["memory_id"] for item in exported] == ["a", "b"]
    assert json.dumps(exported)


def test_delete_and_selected_database_backend_round_trip():
    memory_id = f"agent-memory-test-{uuid.uuid4()}"
    store = AgentMemoryStore()
    record = _github_record(memory_id=memory_id, scope="ci:selected-backend")
    try:
        stored = store.upsert(record)
        assert stored.memory_id == memory_id
        assert store.get(memory_id).source_version == "sha-1"
    finally:
        store.delete(memory_id)
    assert store.get(memory_id) is None
