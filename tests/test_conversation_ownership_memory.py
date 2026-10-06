from pathlib import Path

import pytest

import conversation_ownership as ownership
import db
from agent_memory.conversation_ownership_store import MIGRATION_KEY
from agent_memory.file_memory_db import FileMemoryDB


def _configure_store(monkeypatch, tmp_path: Path) -> Path:
    path = tmp_path / "alice.memory"
    monkeypatch.setenv("ALICE_MEMORY_PATH", str(path))
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "legacy.db"))
    return path


def _insert_legacy_owner(conversation_id: str, user_id: str, created_at: int) -> None:
    conn = db.get_conn()
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS conversation_owners (
                conversation_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                created_at INTEGER NOT NULL
            )"""
        )
        conn.execute(
            """INSERT INTO conversation_owners
               (conversation_id, user_id, created_at)
               VALUES (?, ?, ?)""",
            (conversation_id, user_id, created_at),
        )
        conn.commit()
    finally:
        conn.close()


def test_legacy_owner_is_imported_and_survives_reopen(monkeypatch, tmp_path):
    path = _configure_store(monkeypatch, tmp_path)
    _insert_legacy_owner("conversation-a", "user-a", 123)

    ownership.init_conversation_ownership_table()

    reopened = FileMemoryDB(path)
    assert reopened.get("conversation_owner:conversation-a") == {
        "user_id": "user-a",
        "created_at": 123,
    }
    assert reopened.get(MIGRATION_KEY)["count"] == 1
    assert ownership.get_owner("conversation-a") == "user-a"


def test_completed_migration_no_longer_needs_sql(monkeypatch, tmp_path):
    _configure_store(monkeypatch, tmp_path)
    ownership.init_conversation_ownership_table()
    ownership.set_owner("conversation-a", "user-a")

    import agent_memory.conversation_ownership_migration as migration

    monkeypatch.setattr(
        migration,
        "get_conn",
        lambda: (_ for _ in ()).throw(AssertionError("SQL accessed after cutover")),
    )

    assert ownership.get_owner("conversation-a") == "user-a"


def test_owner_cannot_be_reassigned(monkeypatch, tmp_path):
    _configure_store(monkeypatch, tmp_path)
    ownership.set_owner("conversation-a", "user-a")

    with pytest.raises(PermissionError, match="conversation_not_owned"):
        ownership.set_owner("conversation-a", "user-b")

    assert ownership.get_owner("conversation-a") == "user-a"


def test_owned_conversations_use_typed_db_api(monkeypatch, tmp_path):
    _configure_store(monkeypatch, tmp_path)
    conversations = [
        {"id": "conversation-a", "title": "A", "model": "m", "created_at": 1, "updated_at": 3},
        {"id": "conversation-b", "title": "B", "model": "m", "created_at": 2, "updated_at": 2},
    ]
    monkeypatch.setattr(ownership, "get_conversations", lambda: conversations)

    ownership.set_owner("conversation-a", "user-a")
    ownership.set_owner("conversation-b", "user-b")

    assert ownership.list_owned_conversations("user-a") == [conversations[0]]
    assert ownership.get_owned_conversation("conversation-a", "user-a") == conversations[0]
    assert ownership.get_owned_conversation("conversation-b", "user-a") is None


def test_delete_owner_is_owner_scoped(monkeypatch, tmp_path):
    _configure_store(monkeypatch, tmp_path)
    ownership.set_owner("conversation-a", "user-a")

    ownership.delete_owner("conversation-a", "user-b")
    assert ownership.get_owner("conversation-a") == "user-a"

    ownership.delete_owner("conversation-a", "user-a")
    assert ownership.get_owner("conversation-a") is None


def test_runtime_data_root_wins_over_process_default(monkeypatch, tmp_path):
    monkeypatch.delenv("ALICE_MEMORY_PATH", raising=False)
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "fallback" / "alice.db"))

    from runtime.request_context import bind_runtime_request

    runtime_root = tmp_path / "runtime"
    with bind_runtime_request("runtime-a", "/runtime-a", data_root=str(runtime_root)):
        assert db.memory_file_path() == runtime_root / "alice.memory"


def test_runtime_ownership_module_has_no_direct_sql_dependency():
    source = Path(ownership.__file__).read_text()
    assert "get_conn" not in source
    assert "SELECT " not in source
    assert "INSERT " not in source
    assert "DELETE FROM" not in source


def test_migration_rejects_unexpected_target_record(monkeypatch, tmp_path):
    path = _configure_store(monkeypatch, tmp_path)
    from agent_memory.conversation_ownership_migration import (
        ensure_conversation_ownership_migrated,
    )
    from agent_memory.conversation_ownership_store import ConversationOwnershipStore

    store = ConversationOwnershipStore(FileMemoryDB(path))
    store.set("unexpected", "user-a", 1)

    with pytest.raises(RuntimeError, match="migration verification failed"):
        ensure_conversation_ownership_migrated(store)


def test_migration_rejects_failed_post_copy_verification(monkeypatch):
    import agent_memory.conversation_ownership_migration as migration

    record = {"user_id": "user-a", "created_at": 1}
    monkeypatch.setattr(migration, "_legacy_records", lambda: {"conversation-a": record})

    class BrokenStore:
        def migration_marker(self):
            return None

        def records(self):
            return {}

        def set(self, conversation_id, user_id, created_at):
            return None

        def mark_migrated(self, records):
            raise AssertionError("marker must not be written")

    with pytest.raises(RuntimeError, match="migration verification failed"):
        migration.ensure_conversation_ownership_migrated(BrokenStore())


def test_store_rejects_invalid_ownership_records(tmp_path):
    from agent_memory.conversation_ownership_store import ConversationOwnershipStore

    raw = FileMemoryDB(tmp_path / "alice.memory")
    store = ConversationOwnershipStore(raw)

    raw.put("conversation_owner:bad-shape", "invalid")
    with pytest.raises(ValueError, match="invalid conversation ownership record"):
        store.get("bad-shape")

    raw.put(
        "conversation_owner:bad-fields",
        {"user_id": "", "created_at": "not-an-int"},
    )
    with pytest.raises(ValueError, match="invalid conversation ownership record"):
        store.get("bad-fields")


def test_store_rejects_invalid_migration_markers(tmp_path):
    from agent_memory.conversation_ownership_store import ConversationOwnershipStore

    raw = FileMemoryDB(tmp_path / "alice.memory")
    store = ConversationOwnershipStore(raw)

    raw.put(MIGRATION_KEY, "invalid")
    with pytest.raises(ValueError, match="invalid conversation ownership migration marker"):
        store.migration_marker()

    raw.put(MIGRATION_KEY, {"schema": "1", "count": 0, "digest": ""})
    with pytest.raises(ValueError, match="invalid conversation ownership migration marker"):
        store.migration_marker()


def test_setting_same_owner_is_idempotent(monkeypatch, tmp_path):
    _configure_store(monkeypatch, tmp_path)

    ownership.set_owner("conversation-a", "user-a")
    ownership.set_owner("conversation-a", "user-a")

    assert ownership.get_owner("conversation-a") == "user-a"


def test_delete_missing_owner_is_noop(monkeypatch, tmp_path):
    _configure_store(monkeypatch, tmp_path)

    ownership.delete_owner("missing")

    assert ownership.get_owner("missing") is None
