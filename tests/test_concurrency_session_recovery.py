import json
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor

import db
from invocation_manager import (
    create_invocation,
    fail_invocation,
    finish_invocation,
    get_invocation,
    persist_invocation_trace,
    start_invocation,
)
from invocation_trace import create_invocation_trace
from runtime_migrations import init_runtime_tables
from session_manager import create_session, get_session, restore_session


def setup_db(tmpdir):
    old_path = db.DB_PATH
    db.DB_PATH = os.path.join(tmpdir, "runtime.db")
    db.init_db()
    init_runtime_tables()
    return old_path


def test_parallel_invocations_keep_contexts_and_traces_isolated():
    with tempfile.TemporaryDirectory() as tmpdir:
        old_path = setup_db(tmpdir)
        try:
            create_session("session-a", {"profile": "a"})
            create_session("session-b", {"profile": "b"})
            db.create_conversation("conversation-a", "A", "test-model")
            db.create_conversation("conversation-b", "B", "test-model")

            def make(pair):
                session_id, conversation_id = pair
                return create_invocation(session_id, conversation_id, {"marker": conversation_id})

            with ThreadPoolExecutor(max_workers=2) as pool:
                first, second = list(
                    pool.map(
                        make,
                        [
                            ("session-a", "conversation-a"),
                            ("session-b", "conversation-b"),
                        ],
                    )
                )

            assert first.session_id == "session-a"
            assert first.conversation_id == "conversation-a"
            assert second.session_id == "session-b"
            assert second.conversation_id == "conversation-b"
            assert first.invocation_id != second.invocation_id
            assert first.trace_id != second.trace_id
            assert get_invocation(first.invocation_id)["metadata"]["marker"] == "conversation-a"
            assert get_invocation(second.invocation_id)["metadata"]["marker"] == "conversation-b"
        finally:
            db.DB_PATH = old_path


def test_session_state_can_be_restored_and_reused_by_a_new_invocation():
    with tempfile.TemporaryDirectory() as tmpdir:
        old_path = setup_db(tmpdir)
        try:
            create_session("session-recover", {"step": 1})
            restored = restore_session("session-recover")
            assert restored["status"] == "active"
            assert restored["metadata"]["step"] == 1
            db.create_conversation("conversation-recover", "Recover", "test-model")

            context = create_invocation(
                "session-recover",
                "conversation-recover",
                {"step": 2},
                create_missing_session=False,
            )
            assert context.session_id == "session-recover"
            assert get_session("session-recover")["status"] == "active"
        finally:
            db.DB_PATH = old_path


def test_failure_in_one_invocation_does_not_change_another():
    with tempfile.TemporaryDirectory() as tmpdir:
        old_path = setup_db(tmpdir)
        try:
            create_session("session-1")
            create_session("session-2")
            first = create_invocation("session-1", "conversation-1")
            second = create_invocation("session-2", "conversation-2")

            assert start_invocation(first.invocation_id)
            assert start_invocation(second.invocation_id)
            assert fail_invocation(first.invocation_id, {"error": "boom"})
            assert finish_invocation(second.invocation_id, {"answer": "ok"})

            failed = get_invocation(first.invocation_id)
            completed = get_invocation(second.invocation_id)
            assert failed["status"] == "failed"
            assert completed["status"] == "completed"
            assert failed["trace_id"] != completed["trace_id"]
            assert completed["result"] == {"answer": "ok"}
        finally:
            db.DB_PATH = old_path


def test_persisted_trace_survives_invocation_failure_and_reload():
    with tempfile.TemporaryDirectory() as tmpdir:
        old_path = setup_db(tmpdir)
        try:
            create_session("session-trace")
            context = create_invocation("session-trace", "conversation-trace")
            trace = create_invocation_trace(context)
            trace.add_event("test_event", {"ok": True})
            trace_data = trace.finalize()
            assert persist_invocation_trace(context.invocation_id, trace_data)
            assert fail_invocation(context.invocation_id, {"error": "provider failed"})

            reloaded = get_invocation(context.invocation_id)
            assert reloaded["status"] == "failed"
            assert reloaded["trace"]["trace_id"] == context.trace_id
            assert reloaded["trace"]["events"][0]["type"] == "test_event"
            json.dumps(reloaded["trace"], ensure_ascii=False)
        finally:
            db.DB_PATH = old_path
