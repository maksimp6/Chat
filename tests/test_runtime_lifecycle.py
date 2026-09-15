import json
import sqlite3

import pytest


@pytest.fixture
def runtime_db(tmp_path, monkeypatch):
    import db
    import session_manager
    import invocation_manager
    import runtime_migrations

    path = tmp_path / "runtime.db"
    monkeypatch.setattr(db, "DB_PATH", str(path))
    monkeypatch.setattr(session_manager, "get_conn", db.get_conn)
    monkeypatch.setattr(invocation_manager, "get_conn", db.get_conn)
    monkeypatch.setattr(runtime_migrations, "get_conn", db.get_conn)

    db.init_db()
    runtime_migrations.init_runtime_tables()
    return path


def test_session_restore(runtime_db):
    from session_manager import create_session, restore_session, get_session

    session = create_session(metadata={"profile": "developer"})
    restored = restore_session(session["id"])

    assert restored["id"] == session["id"]
    assert restored["status"] == "active"
    assert restored["metadata"] == {"profile": "developer"}
    assert get_session(session["id"])["id"] == session["id"]


def test_invocation_has_independent_context(runtime_db):
    from session_manager import create_session
    from invocation_manager import create_invocation, start_invocation, finish_invocation, get_invocation

    session = create_session()
    first = create_invocation(session["id"], "conversation-a")
    second = create_invocation(session["id"], "conversation-b")

    assert first.invocation_id != second.invocation_id
    assert first.trace_id != second.trace_id
    assert first.conversation_id != second.conversation_id

    assert start_invocation(first.invocation_id)
    assert finish_invocation(first.invocation_id, {"ok": True})
    assert get_invocation(first.invocation_id)["status"] == "completed"
    assert get_invocation(second.invocation_id)["status"] == "created"


def test_invocation_result_is_json_persisted(runtime_db):
    from session_manager import create_session
    from invocation_manager import create_invocation, finish_invocation, get_invocation

    session = create_session()
    context = create_invocation(session["id"], "conversation-a", {"source": "test"})
    payload = {"nested": [1, 2, {"safe": True}]}
    finish_invocation(context.invocation_id, payload)

    loaded = get_invocation(context.invocation_id)
    assert loaded["result"] == payload
    assert loaded["metadata"] == {"source": "test"}
