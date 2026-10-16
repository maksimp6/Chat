"""Native service rejection, ownership and persisted configuration contracts."""

from types import SimpleNamespace
import runpy
import pytest
import requests
import db
from agent_memory.client import close_memory_clients
from agent_memory.conversation_ownership_store import ConversationOwnershipStore
from agent_memory.repository import AgentMemoryStore
import memory_extractor as extractor
import memory_manager as manager
import sessions.manager as sessions
from invocation import manager as invocations


def test_ownership_cannot_be_reassigned_or_deleted_by_another_owner():
    owners = ConversationOwnershipStore(db.get_store())
    owners.set("chat", "owner", 1)
    owners.set("chat", "owner", 2)
    with pytest.raises(PermissionError):
        owners.set("chat", "other", 3)
    owners.delete("missing")
    owners.delete("chat", "other")
    assert owners.get("chat") == "owner"
    owners.delete("chat", "owner")
    close_memory_clients()
    assert ConversationOwnershipStore(db.get_store()).get("chat") is None


def test_malformed_agent_record_is_rejected():
    store = db.get_store()
    with store.transaction() as tx:
        tx.set("agent_memory:broken", "invalid")
    with pytest.raises(ValueError, match="invalid agent memory"):
        AgentMemoryStore(store).get("broken")


def test_fact_validation_deduplication_and_config_survive_restart():
    extractor.init_global_memory()
    with pytest.raises(ValueError, match="list"):
        extractor.save_global_memory_facts({})
    entries = [
        None,
        {"fact": 7},
        {"fact": " "},
        {"fact": "x" * 8193},
        {"fact": "stable", "category": None},
        {"fact": "stable"},
        {"fact": "path", "category": "paths"},
    ]
    assert extractor.save_global_memory_facts(entries, now=1) == 2
    manager.save_memory_config({"max_context_facts": 1, "categories_allowed": ["paths"]})
    close_memory_clients()
    assert "[PATHS] path" in manager.get_controlled_memory_summary()
    assert "stable" not in manager.get_controlled_memory_summary()
    assert "stable" in extractor.get_global_memory_summary()
    manager.clear_global_memory("paths")
    assert manager.get_controlled_memory_summary() == ""
    manager.clear_global_memory()
    assert extractor.get_global_memory_summary() == ""
    manager.save_memory_config({"enabled": False})
    assert manager.get_controlled_memory_summary() == ""


@pytest.mark.parametrize(
    "config", [None, {"max_context_facts": -1}, {"categories_allowed": "paths"}]
)
def test_invalid_memory_configuration_fails_closed(config):
    if config is None:
        with pytest.raises(ValueError):
            manager.save_memory_config([])
        with db.get_store().transaction() as tx:
            tx.set("global_memory_config", "broken")
        with pytest.raises(ValueError):
            manager.load_memory_config()
        with pytest.raises(ValueError):
            manager.save_memory_config({})
    else:
        manager.save_memory_config(config)
        with pytest.raises(ValueError):
            manager.get_controlled_memory_summary()


def test_malformed_fact_prevents_listing_and_clear():
    with db.get_store().transaction() as tx:
        tx.set("global_fact:broken", "invalid")
    with pytest.raises(ValueError):
        extractor.list_global_memory_facts()
    with pytest.raises(ValueError):
        manager.clear_global_memory()
    assert db.get_store().get("global_fact:broken") == "invalid"


@pytest.mark.parametrize(
    "answer", [None, "http-error", "network-error", "bad-json", "wrong-shape", "valid"]
)
def test_extraction_only_commits_valid_provider_result(answer, monkeypatch):
    monkeypatch.setenv("YANDEX_PROJECT_ID", "project")
    monkeypatch.setenv("YANDEX_API_KEY", "test-key")

    def respond(*args, **kwargs):
        assert kwargs["timeout"] == 30
        if answer == "network-error":
            raise requests.RequestException("offline")
        text = {"bad-json": "{", "wrong-shape": '{"facts": {}}'}.get(
            answer, '{"facts": [{"fact": "durable", "category": "git"}]}'
        )
        return SimpleNamespace(
            status_code=500 if answer == "http-error" else 200,
            json=lambda: {"result": {"alternatives": [{"message": {"text": text}}]}},
        )

    monkeypatch.setattr(extractor.requests, "post", respond)
    messages = [] if answer is None else [{"role": "user", "text": "project"}]
    assert extractor.extract_and_save_facts(messages) == (1 if answer == "valid" else 0)
    close_memory_clients()
    assert len(extractor.list_global_memory_facts()) == (1 if answer == "valid" else 0)


def test_memory_cli_prints_empty_summary(capsys):
    runpy.run_module("memory_extractor", run_name="__main__")
    assert "Память пока пуста" in capsys.readouterr().out


def test_session_rejects_duplicate_invalid_status_and_corrupt_records():
    sessions.create_session("session")
    with pytest.raises(ValueError, match="already exists"):
        sessions.create_session("session")
    with pytest.raises(ValueError, match="Unsupported"):
        sessions.update_session("session", status="invalid")
    assert sessions.update_session("missing") is None
    for operation in (sessions.get_session, sessions.restore_session, sessions.update_session):
        with db.get_store().transaction() as tx:
            tx.set("runtime_session:broken", {"id": "other"})
        with pytest.raises(ValueError, match="invalid runtime"):
            operation("broken")


def test_invocation_repeated_start_missing_trace_and_corrupt_record():
    context = invocations.create_invocation("session", "chat")
    assert invocations.start_invocation(context.invocation_id)
    assert not invocations.start_invocation(context.invocation_id)
    assert not invocations.start_invocation("missing")
    assert not invocations.persist_invocation_trace("missing", {})
    with db.get_store().transaction() as tx:
        tx.set("runtime_invocation:broken", {"id": "other"})
    with pytest.raises(ValueError, match="invalid invocation"):
        invocations.get_invocation("broken")


def test_transaction_deleted_record_reads_as_absent():
    store = db.get_store()
    with store.transaction() as tx:
        tx.set("record", 1)
    with store.transaction() as tx:
        tx.delete("record")
        assert tx.get("record") is None
    close_memory_clients()
    assert db.get_store().get("record") is None
