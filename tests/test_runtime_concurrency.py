from concurrent.futures import ThreadPoolExecutor

import pytest


@pytest.fixture
def runtime_db(tmp_path, monkeypatch):
    import db
    import session_manager
    import invocation_manager
    import runtime_migrations

    path = tmp_path / "concurrency.db"
    monkeypatch.setattr(db, "DB_PATH", str(path))
    monkeypatch.setattr(session_manager, "get_conn", db.get_conn)
    monkeypatch.setattr(invocation_manager, "get_conn", db.get_conn)
    monkeypatch.setattr(runtime_migrations, "get_conn", db.get_conn)

    db.init_db()
    runtime_migrations.init_runtime_tables()
    return path


def test_parallel_invocations_keep_context_and_trace_isolated(runtime_db):
    from session_manager import create_session
    from invocation_manager import create_invocation, finish_invocation, get_invocation

    session = create_session(metadata={"suite": "concurrency"})

    def create(index):
        context = create_invocation(
            session["id"],
            f"conversation-{index}",
            metadata={"index": index},
        )
        finish_invocation(context.invocation_id, {"index": index})
        return context

    with ThreadPoolExecutor(max_workers=8) as pool:
        contexts = list(pool.map(create, range(24)))

    assert len({c.invocation_id for c in contexts}) == 24
    assert len({c.trace_id for c in contexts}) == 24
    assert len({c.conversation_id for c in contexts}) == 24

    for context in contexts:
        loaded = get_invocation(context.invocation_id)
        assert loaded["session_id"] == session["id"]
        assert loaded["conversation_id"] == context.conversation_id
        assert loaded["trace_id"] == context.trace_id
        assert loaded["result"]["index"] == loaded["metadata"]["index"]


def test_parallel_first_use_reuses_one_session_without_losing_invocations(runtime_db):
    from invocation_manager import create_invocation, finish_invocation, get_invocation

    session_id = "first-use-session"

    def create(index):
        context = create_invocation(
            session_id,
            f"first-use-conversation-{index}",
            metadata={"index": index},
        )
        assert finish_invocation(context.invocation_id, {"index": index})
        return context

    with ThreadPoolExecutor(max_workers=8) as pool:
        contexts = list(pool.map(create, range(16)))

    assert len({c.invocation_id for c in contexts}) == 16
    assert all(c.session_id == session_id for c in contexts)
    for context in contexts:
        loaded = get_invocation(context.invocation_id)
        assert loaded["status"] == "completed"
        assert loaded["result"]["index"] == loaded["metadata"]["index"]
