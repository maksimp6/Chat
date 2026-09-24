import json
import os
import unittest
from unittest.mock import patch

import supabase_trace_mirror


class _Response:
    status = 201

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class SupabaseTraceMirrorTests(unittest.TestCase):
    def tearDown(self):
        for name in ("SUPABASE_URL", "SUPABASE_SECRET_KEY", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_ANON_KEY"):
            os.environ.pop(name, None)

    def test_disabled_without_secret_key(self):
        os.environ["SUPABASE_URL"] = "https://example.supabase.co"
        self.assertFalse(supabase_trace_mirror.mirror_trace({"trace_id": "t1"}))

    @patch("supabase_trace_mirror.urlopen")
    def test_uses_secret_key_and_sends_json_safe_payload(self, urlopen):
        os.environ["SUPABASE_URL"] = "https://example.supabase.co"
        os.environ["SUPABASE_SECRET_KEY"] = "server-secret"
        urlopen.return_value = _Response()
        trace = {"trace_id": "t1", "nested": {}}
        trace["nested"]["self"] = trace

        self.assertTrue(supabase_trace_mirror.mirror_trace(trace))
        request = urlopen.call_args.args[0]
        self.assertIn("Bearer server-secret", request.headers.get("Authorization"))
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(body["trace_id"], "t1")
        self.assertEqual(body["payload"]["nested"]["self"], "<cycle omitted>")

    @patch("supabase_trace_mirror.urlopen", side_effect=OSError("offline"))
    def test_network_errors_are_swallowed(self, _urlopen):
        os.environ["SUPABASE_URL"] = "https://example.supabase.co"
        os.environ["SUPABASE_SERVICE_ROLE_KEY"] = "server-secret"
        self.assertFalse(supabase_trace_mirror.mirror_trace({"trace_id": "t1"}))


if __name__ == "__main__":
    unittest.main()
