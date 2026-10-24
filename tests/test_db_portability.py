"""Native runtime persistence contracts after removing SQL migration code."""

import pytest

import invocation.manager as invocation_manager
from agent_memory.client import close_memory_clients
from sessions.manager import get_session


@pytest.fixture
def native_store(tmp_path, monkeypatch):
    import storage.db as db

    path = tmp_path / "native.memory"
    monkeypatch.setenv("LOGOS_MEMORY_PATH", str(path))
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "unused.db"))
    close_memory_clients()
    yield path
    close_memory_clients()


def test_first_use_reuses_session_after_duplicate_create_race(native_store, monkeypatch):
    from sessions.manager import create_session

    real_create = create_session

    def racing_create(*args, **kwargs):
        real_create(*args, **kwargs)
        raise ValueError("Session already exists")

    monkeypatch.setattr(invocation_manager, "create_session", racing_create)
    context = invocation_manager.create_invocation("session-race", "conversation-race")
    assert context.session_id == "session-race"
    assert get_session("session-race")["id"] == "session-race"
    assert invocation_manager.get_invocation(context.invocation_id)["status"] == "created"


def test_first_use_fails_if_duplicate_session_has_no_winner(native_store, monkeypatch):
    monkeypatch.setattr(invocation_manager, "restore_session", lambda _sid: None)

    def duplicate(*args, **kwargs):
        raise ValueError("Session already exists")

    monkeypatch.setattr(invocation_manager, "create_session", duplicate)
    with pytest.raises(ValueError, match="Session already exists"):
        invocation_manager.create_invocation("session-race", "conversation-race")


def test_runtime_rejects_missing_session_without_side_effects(native_store):
    with pytest.raises(ValueError, match="Session not found"):
        invocation_manager.create_invocation(
            "absent", "conversation-race", create_missing_session=False
        )
    assert get_session("absent") is None
