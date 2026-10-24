"""Owner writes use native Kristall transactions, not SQL or adapters."""

from pathlib import Path

import pytest

import conversations.ownership as ownership
import storage.db as db
from agent_memory.conversation_ownership_store import ConversationOwnershipStore
from agent_memory.client import close_memory_clients, get_memory_client


def _configure(monkeypatch, tmp_path):
    path = tmp_path / "logos.memory"
    monkeypatch.setenv("LOGOS_MEMORY_PATH", str(path))
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "unused.db"))
    close_memory_clients()
    return path


def test_owner_survives_reopen_without_sql(monkeypatch, tmp_path):
    path = _configure(monkeypatch, tmp_path)
    ownership.init_conversation_ownership_table()
    ownership.set_owner("conversation-a", "user-a")
    before = get_memory_client(path).last_commit.sequence
    ownership.set_owner("conversation-a", "user-a")
    assert get_memory_client(path).last_commit.sequence == before
    close_memory_clients(path)
    assert ownership.get_owner("conversation-a") == "user-a"
    close_memory_clients(path)


def test_old_owner_metadata_does_not_block_shared_access(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    db.create_conversation("conversation-a", "Old", "m")
    ownership.set_owner("conversation-a", "user-a")
    ownership.set_owner("conversation-a", "user-b")
    assert ownership.get_owner("conversation-a") == "user-a"
    assert ownership.check_access("conversation-a", "user-b")
    assert not ownership.check_access("missing", "user-b")


def test_shared_delete_removes_old_metadata(monkeypatch, tmp_path):
    path = _configure(monkeypatch, tmp_path)
    ownership.set_owner("conversation-a", "user-a")
    before = get_memory_client(path).last_commit.sequence
    ownership.delete_owner("missing", "user-b")
    assert get_memory_client(path).last_commit.sequence == before
    ownership.delete_owner("conversation-a", "user-b")
    assert ownership.get_owner("conversation-a") is None


def test_ownership_records_are_individual_delta_keys(monkeypatch, tmp_path):
    path = _configure(monkeypatch, tmp_path)
    ownership.set_owner("conversation-a", "user-a")
    ownership.set_owner("conversation-b", "user-b")
    raw = get_memory_client(path)
    assert raw.get("conversation_owner:conversation-a")["user_id"] == "user-a"
    assert raw.get("conversation_owner:conversation-b")["user_id"] == "user-b"
    records = ConversationOwnershipStore(raw).records()
    assert set(records) == {"conversation-a", "conversation-b"}
    assert (
        raw.items(prefix="conversation_owner:")["conversation_owner:conversation-a"]
        == records["conversation-a"]
    )


def test_invalid_owner_fails_closed(monkeypatch, tmp_path):
    path = _configure(monkeypatch, tmp_path)
    raw = get_memory_client(path)
    raw.set("conversation_owner:bad", {"user_id": "", "created_at": "invalid"})
    raw.commit()
    with pytest.raises(ValueError, match="invalid conversation ownership record"):
        ownership.get_owner("bad")


def test_all_conversations_are_shared(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    conversations = [
        {"id": "a", "title": "A", "model": "m", "created_at": 1, "updated_at": 3},
        {"id": "b", "title": "B", "model": "m", "created_at": 2, "updated_at": 4},
    ]
    monkeypatch.setattr(ownership, "get_conversations", lambda: conversations)
    ownership.set_owner("a", "owner-a")
    ownership.set_owner("b", "owner-b")
    assert ownership.list_owned_conversations("owner-a") == conversations
    assert ownership.get_owned_conversation("b", "owner-a") == conversations[1]


def test_runtime_data_root_is_preserved(monkeypatch, tmp_path):
    monkeypatch.delenv("LOGOS_MEMORY_PATH", raising=False)
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "fallback" / "logos.db"))
    from runtime.request_context import bind_runtime_request

    root = tmp_path / "runtime"
    with bind_runtime_request("runtime-a", "/runtime-a", data_root=str(root)):
        assert db.memory_file_path() == root / "logos.memory"


def test_module_contains_no_sql_call():
    source = Path(ownership.__file__).read_text(encoding="utf-8")
    assert "get_conn" not in source
    assert "conversation_ownership_migration" not in source
