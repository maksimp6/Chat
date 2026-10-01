"""
RED contract tests for #702: unified sync/async execution mode contract.

All tests fail until agent_office/execution_mode.py is created and implements
the proposed minimal interface below.  The top-level import block is the
immediate RED signal (ImportError) that the Team Lead should see when running
`pytest tests/test_execution_mode_contract.py`.

Proposed minimal public boundary (to be created in agent_office/execution_mode.py):

    ExecutionMode              – Enum  sync | async | auto
    UnifiedDispatcher          – main orchestration entry point
    ExecutionModeSelector      – deterministic auto-mode policy (no LLM)
    TaskDispatchResult         – terminal result returned by sync dispatch
    DurableTaskHandle          – stable identity returned immediately by async dispatch
    DuplicateDispatchSuppressed – raised when idempotency_key already claimed
    ApprovalRequired            – raised when approval gate blocks execution
    ExecutionModeError          – raised for illegal mode/policy inputs

Reuses without modification:
    agent_context.TaskPacket, TaskScope, EvidenceVersion
    trace_manager.ExecutionTrace
    invocation.context.InvocationContext

Does NOT touch:
    - invocation/manager.py (lifecycle persistence — owned by #636)
    - agent_office/task_state.py (evidence state machine — stable)
    - runtime/dispatcher.py (scope isolation — #500/#527)
    - Any production code, workflow, secrets or permissions file.
"""
import uuid
from unittest.mock import MagicMock

import pytest

# ── RED: ImportError until agent_office/execution_mode.py exists ─────────────
from agent_office.execution_mode import (  # noqa: E402
    ApprovalRequired,
    DuplicateDispatchSuppressed,
    DurableTaskHandle,
    ExecutionMode,
    ExecutionModeError,
    ExecutionModeSelector,
    TaskDispatchResult,
    UnifiedDispatcher,
)

# Reuse existing canonical types — no new production imports.
from agent_context import EvidenceVersion, TaskPacket, TaskScope
from invocation.context import InvocationContext
from trace_manager import ExecutionTrace


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    import db
    import invocation.manager as invocation_manager
    import runtime_migrations
    import session_manager

    path = tmp_path / "test702.db"
    monkeypatch.setattr(db, "DB_PATH", str(path))
    monkeypatch.setattr(session_manager, "get_conn", db.get_conn)
    monkeypatch.setattr(invocation_manager, "get_conn", db.get_conn)
    monkeypatch.setattr(runtime_migrations, "get_conn", db.get_conn)
    db.init_db()
    runtime_migrations.init_runtime_tables()
    yield path


def _scope(head_sha=None):
    return TaskScope(
        repository="test/repo",
        work_item="issue-702",
        base_sha="a" * 40,
        head_sha=head_sha or "b" * 40,
        role="backend-engineer",
    )


def _packet(idempotency_key=None, head_sha=None):
    p = TaskPacket(
        scope=_scope(head_sha=head_sha),
        evidence=EvidenceVersion(task="1.0"),
        objective="test objective",
        expected_deliverable="test deliverable",
    )
    return p, idempotency_key or str(uuid.uuid4())


def _ctx():
    return InvocationContext.create(
        session_id=str(uuid.uuid4()),
        conversation_id=str(uuid.uuid4()),
    )


def _ok_worker(**_):
    return {"result": "ok", "artifacts": []}


# ─── 1. Sync bounded execution ───────────────────────────────────────────────

def test_sync_returns_one_terminal_result_and_finalized_trace(isolated_db):
    """#702 req: sync task returns one terminal result and trace."""
    packet, key = _packet()
    worker = MagicMock(side_effect=_ok_worker)
    dispatcher = UnifiedDispatcher(db_path=str(isolated_db))

    result = dispatcher.dispatch(
        packet, ExecutionMode.SYNC,
        context=_ctx(), idempotency_key=key, worker=worker,
    )

    assert isinstance(result, TaskDispatchResult)
    assert result.status in {"completed", "failed", "cancelled"}, (
        f"unexpected terminal status: {result.status!r}"
    )
    assert result.deliverable is not None
    assert isinstance(result.trace, ExecutionTrace)
    assert result.trace.finalized, "trace must be finalized after sync dispatch"
    worker.assert_called_once()


def test_sync_timeout_does_not_falsely_report_completed(isolated_db):
    """#702 req: sync timeout must not falsely report completed work."""
    import time

    packet, key = _packet()
    dispatcher = UnifiedDispatcher(db_path=str(isolated_db))

    def slow_worker(**_):
        time.sleep(5)
        return {"result": "late"}

    with pytest.raises((TimeoutError, ExecutionModeError)):
        dispatcher.dispatch(
            packet, ExecutionMode.SYNC,
            context=_ctx(), idempotency_key=key, worker=slow_worker,
            deadline_seconds=0.05,
        )

    handle = dispatcher.get_handle(key)
    assert handle is not None
    assert handle.status != "completed", (
        "a timed-out task must never be stored with status 'completed'"
    )


# ─── 2. Async durable execution ─────────────────────────────────────────────

def test_async_returns_task_id_before_worker_executes(isolated_db):
    """#702 req: async dispatch durably returns task id before work finishes."""
    packet, key = _packet()
    worker = MagicMock(side_effect=_ok_worker)
    dispatcher = UnifiedDispatcher(db_path=str(isolated_db))

    handle = dispatcher.dispatch(
        packet, ExecutionMode.ASYNC,
        context=_ctx(), idempotency_key=key, worker=worker,
    )

    assert isinstance(handle, DurableTaskHandle)
    assert handle.task_id, "task_id must be a non-empty string"
    assert handle.status in {"accepted", "queued"}
    worker.assert_not_called()


def test_async_durable_acceptance_persisted_before_return(isolated_db):
    """#702 req: async acknowledgement must happen only after durable acceptance."""
    packet, key = _packet()
    dispatcher = UnifiedDispatcher(db_path=str(isolated_db))

    handle = dispatcher.dispatch(
        packet, ExecutionMode.ASYNC,
        context=_ctx(), idempotency_key=key, worker=MagicMock(),
    )

    # Reconstruct a fresh dispatcher over the same DB — simulates a new process.
    dispatcher2 = UnifiedDispatcher(db_path=str(isolated_db))
    persisted = dispatcher2.get_handle(key)
    assert persisted is not None, "handle must be persisted before dispatch returns"
    assert persisted.task_id == handle.task_id


def test_async_replay_resumes_without_duplicate_worker_invocation(isolated_db):
    """#702 req: process restart/replay resumes async task without duplicate dispatch."""
    packet, key = _packet()
    worker = MagicMock(side_effect=_ok_worker)
    dispatcher = UnifiedDispatcher(db_path=str(isolated_db))

    handle1 = dispatcher.dispatch(
        packet, ExecutionMode.ASYNC,
        context=_ctx(), idempotency_key=key, worker=worker,
    )

    # Simulate process restart — new dispatcher instance, same DB.
    dispatcher2 = UnifiedDispatcher(db_path=str(isolated_db))
    handle2 = dispatcher2.dispatch(
        packet, ExecutionMode.ASYNC,
        context=_ctx(), idempotency_key=key, worker=worker,
    )

    assert handle2.task_id == handle1.task_id, "replay must return the same task id"
    assert worker.call_count <= 1, (
        f"worker must not be invoked twice on replay; call_count={worker.call_count}"
    )


# ─── 3. Shared result/trace schema ──────────────────────────────────────────

def test_sync_and_async_produce_equivalent_deliverable_schema(isolated_db):
    """#702 req: same logical task has equivalent final deliverable schema in both modes."""
    packet_s, key_s = _packet()
    packet_a, key_a = _packet()
    dispatcher = UnifiedDispatcher(db_path=str(isolated_db))

    sync_result = dispatcher.dispatch(
        packet_s, ExecutionMode.SYNC,
        context=_ctx(), idempotency_key=key_s, worker=MagicMock(side_effect=_ok_worker),
    )
    async_handle = dispatcher.dispatch(
        packet_a, ExecutionMode.ASYNC,
        context=_ctx(), idempotency_key=key_a, worker=MagicMock(side_effect=_ok_worker),
    )
    async_result = dispatcher.complete_async(async_handle)

    assert isinstance(async_result, TaskDispatchResult)
    sync_keys = set(sync_result.deliverable.keys())
    async_keys = set(async_result.deliverable.keys())
    assert sync_keys == async_keys, (
        f"deliverable schema differs: sync={sync_keys} async={async_keys}"
    )


def test_sync_and_async_traces_are_independent(isolated_db):
    """#702 req: sync and async invocations preserve independent trace/context."""
    packet_s, key_s = _packet()
    packet_a, key_a = _packet()
    dispatcher = UnifiedDispatcher(db_path=str(isolated_db))

    result_s = dispatcher.dispatch(
        packet_s, ExecutionMode.SYNC,
        context=_ctx(), idempotency_key=key_s, worker=MagicMock(side_effect=_ok_worker),
    )
    handle_a = dispatcher.dispatch(
        packet_a, ExecutionMode.ASYNC,
        context=_ctx(), idempotency_key=key_a, worker=MagicMock(),
    )

    assert result_s.trace.trace_id is not None
    assert handle_a.trace_id is not None
    assert result_s.trace.trace_id != handle_a.trace_id, (
        "each dispatch must have its own trace_id"
    )


# ─── 4. Duplicate-dispatch suppression ──────────────────────────────────────

def test_duplicate_idempotency_key_suppressed_before_paid_execution(isolated_db):
    """#702 req: duplicate async dispatch key produces no second paid execution."""
    packet, key = _packet()
    worker = MagicMock(side_effect=_ok_worker)
    dispatcher = UnifiedDispatcher(db_path=str(isolated_db))

    dispatcher.dispatch(
        packet, ExecutionMode.SYNC,
        context=_ctx(), idempotency_key=key, worker=worker,
    )

    with pytest.raises(DuplicateDispatchSuppressed) as exc_info:
        dispatcher.dispatch(
            packet, ExecutionMode.SYNC,
            context=_ctx(), idempotency_key=key, worker=worker,
        )

    assert exc_info.value.task_id, "suppressed exception must carry the existing task_id"
    assert worker.call_count == 1, (
        f"worker must be called exactly once; call_count={worker.call_count}"
    )


# ─── 5. Bounded fork/join and sibling isolation ──────────────────────────────

def test_bounded_async_fan_out_and_deterministic_join(isolated_db):
    """#702 req: bounded async fan-out + deterministic join."""
    parent_packet, parent_key = _packet()
    dispatcher = UnifiedDispatcher(db_path=str(isolated_db))
    child_workers = [MagicMock(side_effect=_ok_worker) for _ in range(3)]

    parent_handle = dispatcher.fork(
        parent_packet,
        context=_ctx(),
        idempotency_key=parent_key,
        child_workers=child_workers,
        max_children=5,
    )

    assert isinstance(parent_handle, DurableTaskHandle)
    children = dispatcher.get_children(parent_handle.task_id)
    assert 1 <= len(children) <= 5, (
        f"fan-out must be bounded: found {len(children)} children"
    )
    assert len(children) == 3

    join_result = dispatcher.join(parent_handle.task_id)
    assert isinstance(join_result, TaskDispatchResult)
    assert join_result.status in {"completed", "failed"}
    assert len(join_result.child_results) == 3


def test_child_cancellation_does_not_corrupt_siblings(isolated_db):
    """#702 req: cancellation of one child does not corrupt siblings."""
    parent_packet, parent_key = _packet()
    dispatcher = UnifiedDispatcher(db_path=str(isolated_db))

    child_workers = [
        MagicMock(side_effect=_ok_worker),
        MagicMock(side_effect=RuntimeError("intentional failure")),
        MagicMock(side_effect=_ok_worker),
    ]

    parent_handle = dispatcher.fork(
        parent_packet,
        context=_ctx(),
        idempotency_key=parent_key,
        child_workers=child_workers,
        max_children=5,
    )
    children = dispatcher.get_children(parent_handle.task_id)
    dispatcher.cancel_child(children[1].task_id)

    siblings = [c for c in dispatcher.get_children(parent_handle.task_id)
                if c.task_id != children[1].task_id]
    for sibling in siblings:
        assert sibling.status not in {"cancelled", "failed"}, (
            f"sibling {sibling.task_id} must not be corrupted by unrelated child cancellation"
        )


def test_unbounded_fan_out_is_rejected(isolated_db):
    """#702 req: no unbounded agent spawning — must raise ExecutionModeError."""
    parent_packet, parent_key = _packet()
    dispatcher = UnifiedDispatcher(db_path=str(isolated_db))

    with pytest.raises(ExecutionModeError, match="max_children"):
        dispatcher.fork(
            parent_packet,
            context=_ctx(),
            idempotency_key=parent_key,
            child_workers=[MagicMock() for _ in range(10)],
            max_children=3,
        )


# ─── 6. Approval-required gate ───────────────────────────────────────────────

def test_approval_required_stops_sync_before_worker(isolated_db):
    """#702 req: approval-required stops sync mode at the same policy boundary."""
    packet, key = _packet()
    worker = MagicMock(side_effect=_ok_worker)
    dispatcher = UnifiedDispatcher(db_path=str(isolated_db))

    with pytest.raises(ApprovalRequired) as exc_info:
        dispatcher.dispatch(
            packet, ExecutionMode.SYNC,
            context=_ctx(), idempotency_key=key, worker=worker,
            approval_required=True,
        )

    worker.assert_not_called()
    assert exc_info.value.task_id, "ApprovalRequired must carry the task_id"


def test_approval_required_stops_async_before_worker(isolated_db):
    """#702 req: approval-required stops async mode at the same policy boundary."""
    packet, key = _packet()
    worker = MagicMock(side_effect=_ok_worker)
    dispatcher = UnifiedDispatcher(db_path=str(isolated_db))

    with pytest.raises(ApprovalRequired) as exc_info:
        dispatcher.dispatch(
            packet, ExecutionMode.ASYNC,
            context=_ctx(), idempotency_key=key, worker=worker,
            approval_required=True,
        )

    worker.assert_not_called()
    assert exc_info.value.task_id, "ApprovalRequired must carry the task_id"


# ─── 7. Deterministic auto-mode selection ────────────────────────────────────

def test_auto_selects_async_when_waiting_on_ci():
    """#702 req: auto mode selects async for lifecycle waiting on CI, records reason."""
    selector = ExecutionModeSelector()

    mode, reason = selector.select(
        expected_latency_seconds=None,
        waits_on_ci=True,
        waits_on_review=False,
        has_independent_children=False,
        approval_required=False,
        deadline_seconds=None,
    )

    assert mode == ExecutionMode.ASYNC
    assert reason, "reason must be non-empty"
    reason_lower = reason.lower()
    assert any(kw in reason_lower for kw in ("ci", "review", "external", "wait")), (
        f"reason must reference the CI/review dependency; got: {reason!r}"
    )


def test_auto_selects_async_when_waiting_on_review():
    """#702 req: auto mode selects async for lifecycle waiting on review."""
    selector = ExecutionModeSelector()

    mode, reason = selector.select(
        expected_latency_seconds=None,
        waits_on_ci=False,
        waits_on_review=True,
        has_independent_children=False,
        approval_required=False,
        deadline_seconds=None,
    )

    assert mode == ExecutionMode.ASYNC
    assert reason


def test_auto_selects_sync_for_bounded_fast_work():
    """#702 req: auto mode selects sync for bounded work within deadline."""
    selector = ExecutionModeSelector()

    mode, reason = selector.select(
        expected_latency_seconds=2.0,
        waits_on_ci=False,
        waits_on_review=False,
        has_independent_children=False,
        approval_required=False,
        deadline_seconds=30.0,
    )

    assert mode == ExecutionMode.SYNC
    assert reason


def test_auto_mode_is_purely_deterministic():
    """#702 req: auto mode cannot use an opaque LLM-only guess."""
    selector = ExecutionModeSelector()

    kwargs = dict(
        expected_latency_seconds=10.0,
        waits_on_ci=True,
        waits_on_review=False,
        has_independent_children=True,
        approval_required=False,
        deadline_seconds=60.0,
    )

    out1 = selector.select(**kwargs)
    out2 = selector.select(**kwargs)
    assert out1 == out2, (
        "auto mode must be purely deterministic for identical policy inputs"
    )


def test_auto_mode_records_resolved_mode_in_trace(isolated_db):
    """#702 req: the chosen mode and reason must be visible in task/trace evidence."""
    packet, key = _packet()
    dispatcher = UnifiedDispatcher(db_path=str(isolated_db))

    result = dispatcher.dispatch(
        packet,
        ExecutionMode.AUTO,
        context=_ctx(),
        idempotency_key=key,
        worker=MagicMock(side_effect=_ok_worker),
        auto_mode_policy={
            "expected_latency_seconds": 1.0,
            "waits_on_ci": False,
            "waits_on_review": False,
            "has_independent_children": False,
            "approval_required": False,
            "deadline_seconds": 30.0,
        },
    )

    events = result.trace.trace.get("events", [])
    mode_events = [e for e in events if e.get("event_type") == "execution_mode_selected"]
    assert len(mode_events) == 1, (
        f"exactly one execution_mode_selected event required; found {len(mode_events)}"
    )
    payload = mode_events[0]["payload"]
    assert "mode" in payload, "event payload must contain 'mode'"
    assert "reason" in payload, "event payload must contain 'reason'"
    assert payload["mode"] != "auto", "resolved mode must not remain 'auto'"


# ─── 8. Exact-head freshness rules (identical in both modes) ─────────────────

@pytest.mark.parametrize("mode", [ExecutionMode.SYNC, ExecutionMode.ASYNC])
def test_stale_head_sha_rejected_in_both_modes(isolated_db, mode):
    """#702 req: exact-head freshness rules are identical in both modes."""
    stale_head = "stale" + "0" * 35
    current_head = "current" + "0" * 33
    packet, key = _packet(head_sha=stale_head)
    dispatcher = UnifiedDispatcher(db_path=str(isolated_db))

    with pytest.raises(ExecutionModeError, match="head") as exc_info:
        dispatcher.dispatch(
            packet, mode,
            context=_ctx(),
            idempotency_key=str(uuid.uuid4()),
            worker=MagicMock(side_effect=_ok_worker),
            current_head_sha=current_head,
        )

    assert exc_info.value.mode == mode, (
        f"head-freshness error must record the failing mode; got {exc_info.value.mode!r}"
    )


# ─── 9. Terminal async: one meaningful final status ───────────────────────────

def test_terminal_async_emits_exactly_one_final_status_event(isolated_db):
    """#702 req: terminal async completion emits one meaningful final status, no empty repeats."""
    packet, key = _packet()
    dispatcher = UnifiedDispatcher(db_path=str(isolated_db))

    handle = dispatcher.dispatch(
        packet, ExecutionMode.ASYNC,
        context=_ctx(), idempotency_key=key, worker=MagicMock(side_effect=_ok_worker),
    )
    final_result = dispatcher.complete_async(handle)

    events = final_result.trace.trace.get("events", [])
    terminal_events = [e for e in events if e.get("event_type") == "task_terminal"]
    assert len(terminal_events) == 1, (
        f"exactly one task_terminal event required; found {len(terminal_events)}"
    )
    status = terminal_events[0]["payload"].get("status", "")
    assert status in {"completed", "failed", "cancelled"}, (
        f"terminal event status must be meaningful; got {status!r}"
    )
