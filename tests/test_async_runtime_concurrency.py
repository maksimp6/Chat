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
    from invocation.manager import (
        create_invocation,
        finish_invocation,
        get_invocation,
        persist_invocation_trace,
    )
    from invocation.trace import create_invocation_trace
    from session_manager import create_session
    from trace_manager import bind_current_trace, get_current_trace, reset_current_trace

    session = await asyncio.to_thread(create_session, metadata={"suite": "async-concurrency"})

    async def run(index):
        context = await asyncio.to_thread(
            create_invocation,
            session["id"],
            f"async-conversation-{index}",
            metadata={"index": index},
        )
        trace = create_invocation_trace(context)
        token = bind_current_trace(trace)
        try:
            await asyncio.sleep(0)
            assert get_current_trace() is trace
            trace.add_event("async_boundary_crossed", {"index": index})
            await asyncio.sleep(0)
            assert get_current_trace() is trace
            await asyncio.to_thread(
                persist_invocation_trace,
                context.invocation_id,
                trace,
            )
        finally:
            reset_current_trace(token)
        await asyncio.to_thread(
            finish_invocation,
            context.invocation_id,
            {"index": index},
        )
        return context

    contexts = await asyncio.gather(*(run(index) for index in range(24)))

    assert len({context.invocation_id for context in contexts}) == 24
    assert len({context.trace_id for context in contexts}) == 24
    assert len({context.conversation_id for context in contexts}) == 24

    loaded = await asyncio.gather(
        *(asyncio.to_thread(get_invocation, context.invocation_id) for context in contexts)
    )
    for context, item in zip(contexts, loaded):
        assert item["id"] == context.invocation_id
        assert item["session_id"] == context.session_id
        assert item["conversation_id"] == context.conversation_id
        assert item["trace_id"] == context.trace_id
        assert item["trace"]["trace_id"] == context.trace_id
        assert item["trace"]["context"]["invocation_id"] == context.invocation_id
        assert item["trace"]["context"]["conversation_id"] == context.conversation_id
        assert item["result"]["index"] == item["metadata"]["index"]
        trace_context = item["trace"]["context"]
        assert trace_context["invocation_id"] == context.invocation_id
        assert trace_context["session_id"] == context.session_id
        assert trace_context["conversation_id"] == context.conversation_id
        assert trace_context["trace_id"] == context.trace_id
        assert any(
            event.get("type") == "async_boundary_crossed" for event in item["trace"]["events"]
        )

    assert get_current_trace() is None


@pytest.mark.asyncio
async def test_cancelled_task_does_not_corrupt_sibling_invocation(async_runtime_db):
    from invocation.manager import (
        cancel_invocation,
        create_invocation,
        finish_invocation,
        get_invocation,
        start_invocation,
    )
    from session_manager import create_session

    session = await asyncio.to_thread(create_session, metadata={"suite": "async-cancellation"})
    entered = threading.Event()
    release = threading.Event()
    worker_finished = threading.Event()
    finish_results = []
    cancelled_contexts = []

    def blocked_worker():
        context = create_invocation(
            session["id"],
            "cancelled-conversation",
            metadata={"role": "cancelled"},
        )
        assert start_invocation(context.invocation_id)
        cancelled_contexts.append(context)
        entered.set()
        try:
            if not release.wait(timeout=5):
                raise TimeoutError("cancelled invocation worker was not released")
            finish_results.append(
                cancel_invocation(context.invocation_id, {"reason": "awaiter-cancelled"})
            )
        finally:
            worker_finished.set()
        return context

    cancelled_task = asyncio.create_task(asyncio.to_thread(blocked_worker))
    assert await asyncio.to_thread(entered.wait, 5)

    sibling_context = await asyncio.to_thread(
        create_invocation,
        session["id"],
        "sibling-conversation",
        metadata={"role": "sibling"},
    )
    assert await asyncio.to_thread(start_invocation, sibling_context.invocation_id)

    cancelled_context = cancelled_contexts[0]
    assert await asyncio.to_thread(
        cancel_invocation,
        cancelled_context.invocation_id,
        {"reason": "test-cancellation"},
    )

    cancelled_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled_task

    assert await asyncio.to_thread(
        finish_invocation,
        sibling_context.invocation_id,
        {"role": "sibling"},
    )

    release.set()
    assert await asyncio.to_thread(worker_finished.wait, 5)
    assert finish_results == [False]

    cancelled_invocation = await asyncio.to_thread(get_invocation, cancelled_context.invocation_id)
    assert cancelled_invocation["status"] == "cancelled"
    assert cancelled_invocation["conversation_id"] == cancelled_context.conversation_id
    assert cancelled_invocation["trace_id"] == cancelled_context.trace_id

    sibling = await asyncio.to_thread(get_invocation, sibling_context.invocation_id)
    assert sibling["status"] == "completed"
    assert sibling["conversation_id"] == sibling_context.conversation_id
    assert sibling["trace_id"] == sibling_context.trace_id
    assert sibling["result"] == {"role": "sibling"}


@pytest.mark.asyncio
async def test_async_harness_leaves_no_owned_pending_tasks(async_runtime_db):
    current = asyncio.current_task()
    before = set(asyncio.all_tasks())

    async def short_task():
        await asyncio.sleep(0)

    tasks = [asyncio.create_task(short_task()) for _ in range(8)]
    await asyncio.gather(*tasks)
    await asyncio.sleep(0)

    after = set(asyncio.all_tasks())
    newly_pending = {task for task in after - before if task is not current and not task.done()}
    assert newly_pending == set()
