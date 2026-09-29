import asyncio
import threading

import pytest


@pytest.fixture
def async_runtime_db(tmp_path, monkeypatch):
    import db
    import invocation.manager as invocation_manager
    import runtime_migrations
    import session_manager

    path = tmp_path / "async-concurrency.db"
    monkeypatch.setattr(db, "DB_PATH", str(path))
    monkeypatch.setattr(session_manager, "get_conn", db.get_conn)
    monkeypatch.setattr(invocation_manager, "get_conn", db.get_conn)
    monkeypatch.setattr(runtime_migrations, "get_conn", db.get_conn)

    db.init_db()
    runtime_migrations.init_runtime_tables()
    return path


@pytest.mark.asyncio
async def test_async_tasks_keep_invocation_and_trace_context_isolated(async_runtime_db):
    from invocation.manager import create_invocation, finish_invocation, get_invocation
    from session_manager import create_session

    session = await asyncio.to_thread(create_session, metadata={"suite": "async-concurrency"})

    async def run(index):
        context = await asyncio.to_thread(
            create_invocation,
            session["id"],
            f"async-conversation-{index}",
            metadata={"index": index},
        )
        await asyncio.sleep(0)
        await asyncio.to_thread(finish_invocation, context.invocation_id, {"index": index})
        return context

    contexts = await asyncio.gather(*(run(index) for index in range(24)))

    assert len({context.invocation_id for context in contexts}) == 24
    assert len({context.trace_id for context in contexts}) == 24
    assert len({context.conversation_id for context in contexts}) == 24

    loaded = await asyncio.gather(
        *(asyncio.to_thread(get_invocation, context.invocation_id) for context in contexts)
    )
    for item in loaded:
        assert item["result"]["index"] == item["metadata"]["index"]


@pytest.mark.asyncio
async def test_cancelled_task_does_not_corrupt_sibling_invocation(async_runtime_db):
    from invocation.manager import create_invocation, finish_invocation, get_invocation
    from session_manager import create_session

    session = await asyncio.to_thread(create_session, metadata={"suite": "async-cancellation"})
    entered = threading.Event()
    release = threading.Event()

    def blocked_create():
        context = create_invocation(
            session["id"],
            "cancelled-conversation",
            metadata={"role": "cancelled"},
        )
        entered.set()
        release.wait(timeout=5)
        finish_invocation(context.invocation_id, {"role": "cancelled"})
        return context

    cancelled = asyncio.create_task(asyncio.to_thread(blocked_create))
    assert await asyncio.to_thread(entered.wait, 5)

    sibling_context = await asyncio.to_thread(
        create_invocation,
        session["id"],
        "sibling-conversation",
        metadata={"role": "sibling"},
    )
    await asyncio.to_thread(
        finish_invocation,
        sibling_context.invocation_id,
        {"role": "sibling"},
    )

    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled

    release.set()
    await asyncio.sleep(0.05)

    sibling = await asyncio.to_thread(get_invocation, sibling_context.invocation_id)
    assert sibling["status"] == "completed"
    assert sibling["result"] == {"role": "sibling"}


@pytest.mark.asyncio
async def test_async_harness_leaves_no_owned_pending_tasks(async_runtime_db):
    async def short_task():
        await asyncio.sleep(0)

    tasks = [asyncio.create_task(short_task()) for _ in range(8)]
    await asyncio.gather(*tasks)

    assert all(task.done() for task in tasks)
