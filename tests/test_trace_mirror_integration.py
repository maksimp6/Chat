import unittest
from unittest.mock import patch

import trace_mirror_integration
from trace_manager import ExecutionTrace


class TraceMirrorIntegrationTests(unittest.TestCase):
    def test_finalize_mirrors_trace_once_and_returns_finalized_data(self):
        trace = ExecutionTrace(trace_id="integration-test-trace")

        with patch.object(trace_mirror_integration, "mirror_finalized_trace", return_value=True) as mirror:
            result = trace.finalize()
            second_result = trace.finalize()

        self.assertEqual(result["trace_id"], "integration-test-trace")
        self.assertEqual(second_result["trace_id"], "integration-test-trace")
        mirror.assert_called_once_with(result)


if __name__ == "__main__":
    unittest.main()
