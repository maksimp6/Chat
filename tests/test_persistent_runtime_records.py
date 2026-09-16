import json
import os
import tempfile
import unittest

import db
from invocation_manager import (
    cancel_invocation,
    create_invocation,
    fail_invocation,
    finish_invocation,
    get_invocation,
    start_invocation,
)
from runtime_migrations import init_runtime_tables
from session_manager import create_session, get_session, restore_session, update_session


class PersistentRuntimeRecordsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_db_path = db.DB_PATH
        db.DB_PATH = os.path.join(self.temp_dir.name, "runtime.db")
        db.init_db()
        init_runtime_tables()
        db.create_conversation("conversation-1", "Test", "test-model")
        db.create_conversation("conversation-2", "Test 2", "test-model")

    def tearDown(self):
        db.DB_PATH = self.old_db_path
        self.temp_dir.cleanup()

    def test_session_can_be_created_restored_and_updated(self):
        session = create_session("session-1", {"workflow": "agent", "api_key": "must-not-persist"})
        self.assertEqual(session["id"], "session-1")
        self.assertEqual(session["status"], "active")
        self.assertEqual(session["metadata"], {"workflow": "agent"})
        self.assertIsNotNone(session["created_at"])

        restored = restore_session("session-1")
        self.assertEqual(restored["metadata"]["workflow"], "agent")

        updated = update_session("session-1", {"workflow": "agent", "step": 2}, status="completed")
        self.assertEqual(updated["status"], "completed")
        self.assertEqual(updated["metadata"]["step"], 2)
        self.assertIsNotNone(updated["completed_at"])

        reloaded = get_session("session-1")
        self.assertEqual(reloaded["status"], "completed")
        self.assertEqual(reloaded["metadata"]["step"], 2)
        self.assertIsNotNone(reloaded["completed_at"])

    def test_invocation_can_be_created_updated_and_reloaded(self):
        context = create_invocation(
            "session-1",
            "conversation-1",
            {"model": "test-model", "authorization": "must-not-persist"},
        )
        self.assertEqual(context.session_id, "session-1")
        self.assertEqual(context.conversation_id, "conversation-1")

        created = get_invocation(context.invocation_id)
        self.assertEqual(created["status"], "created")
        self.assertEqual(created["trace_id"], context.trace_id)
        self.assertEqual(created["metadata"], {"model": "test-model"})

        self.assertTrue(start_invocation(context.invocation_id))
        running = get_invocation(context.invocation_id)
        self.assertEqual(running["status"], "running")
        self.assertIsNotNone(running["started_at"])

        self.assertTrue(finish_invocation(context.invocation_id, {"answer": "ok", "token": "secret"}))
        completed = get_invocation(context.invocation_id)
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["result"], {"answer": "ok"})
        self.assertIsNotNone(completed["completed_at"])
        json.dumps(completed, ensure_ascii=False)

    def test_failed_and_cancelled_invocations_persist_terminal_status(self):
        failed = create_invocation("session-1", "conversation-1")
        self.assertTrue(fail_invocation(failed.invocation_id, {"error": "boom", "api_key": "secret"}))
        failed_record = get_invocation(failed.invocation_id)
        self.assertEqual(failed_record["status"], "failed")
        self.assertEqual(failed_record["error"], {"error": "boom"})

        cancelled = create_invocation("session-1", "conversation-1")
        self.assertTrue(cancel_invocation(cancelled.invocation_id, {"reason": "user", "password": "secret"}))
        cancelled_record = get_invocation(cancelled.invocation_id)
        self.assertEqual(cancelled_record["status"], "cancelled")
        self.assertEqual(cancelled_record["error"], {"reason": "user"})

    def test_two_sessions_remain_isolated(self):
        first = create_invocation("session-1", "conversation-1", {"state": "first"})
        second = create_invocation("session-2", "conversation-2", {"state": "second"})

        self.assertNotEqual(first.invocation_id, second.invocation_id)
        self.assertEqual(get_session("session-1")["id"], "session-1")
        self.assertEqual(get_session("session-2")["id"], "session-2")
        self.assertEqual(get_invocation(first.invocation_id)["session_id"], "session-1")
        self.assertEqual(get_invocation(second.invocation_id)["session_id"], "session-2")
        self.assertNotEqual(get_invocation(first.invocation_id)["trace_id"], get_invocation(second.invocation_id)["trace_id"])


if __name__ == "__main__":
    unittest.main()
