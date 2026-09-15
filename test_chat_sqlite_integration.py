"""Real SQLite integration test for failed /api/chat partial output persistence."""
import os
import tempfile
import unittest
from unittest.mock import patch

import db
import mcp_routes


class TestChatSQLiteIntegration(unittest.TestCase):
    def test_partial_output_and_trace_survive_sqlite_reload(self):
        from app import app

        conversation_id = "sqlite-integration-test"

        class FakeClient:
            def ask_with_mcp(self, message, model_key, conversation_id, params):
                trace = params["execution_trace"]
                trace.add_response(
                    {
                        "output": [
                            {
                                "type": "message",
                                "content": [
                                    {"type": "output_text", "text": "Generated before failure"}
                                ],
                            }
                        ]
                    },
                    step_index=1,
                )
                return {"output": []}

            @staticmethod
            def extract_reasoning_and_text(_response):
                raise RuntimeError("Failure after model response")

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "integration.db")
            with patch.object(db, "DB_PATH", db_path), \
                 patch.object(mcp_routes, "AliceClient", lambda _config: FakeClient()):
                db.init_db()
                db.create_conversation(conversation_id, "SQLite integration test", "aliceai-llm")

                app.config["TESTING"] = True
                with app.test_client() as client:
                    response = client.post(
                        "/api/chat",
                        json={
                            "conversation_id": conversation_id,
                            "message": "trigger failure",
                            "model": "aliceai-llm",
                            "params": {},
                        },
                    )

                self.assertEqual(response.status_code, 500)
                payload = response.get_json()
                self.assertEqual(payload["partial_output"], "Generated before failure")
                self.assertIn("Generated before failure", payload["reply"])
                self.assertIn("Failure after model response", payload["reply"])
                self.assertTrue(payload["trace"]["responses"])
                self.assertTrue(payload["trace"]["errors"])

                # Read through the real persistence API, not a mock.
                messages = db.get_messages(conversation_id)
                assistant_messages = [m for m in messages if m["role"] == "assistant"]
                self.assertEqual(len(assistant_messages), 1)

                saved = assistant_messages[0]
                self.assertEqual(saved["text"], payload["reply"])
                self.assertEqual(saved["trace"]["trace_id"], payload["trace"]["trace_id"])
                self.assertEqual(
                    saved["trace"]["responses"][0]["raw"]["output"][0]["content"][0]["text"],
                    "Generated before failure",
                )
                self.assertTrue(saved["trace"]["errors"])

                # Prove the data is really on disk and can be loaded by a new
                # connection after the request has completed.
                self.assertTrue(os.path.exists(db_path))
                reloaded = db.get_messages(conversation_id)
                self.assertEqual(reloaded, messages)


if __name__ == "__main__":
    unittest.main()
