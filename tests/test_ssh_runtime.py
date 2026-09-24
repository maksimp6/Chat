import subprocess
import unittest
from unittest.mock import patch

from ssh_runtime import SSHRuntime, SSHRuntimeError


class SSHRuntimeTests(unittest.TestCase):
    def runtime(self):
        return SSHRuntime(
            {
                "preview": {
                    "host": "preview.example",
                    "port": 2222,
                    "default_user": "alice-agent",
                    "allowed_users": ["alice-agent", "deploy"],
                    "identity_map": {"owner-1": "alice-agent"},
                    "identity_file": "/keys/alice",
                    "known_hosts": "/keys/known_hosts",
                    "command_timeout_seconds": 15,
                }
            }
        )

    @patch("ssh_runtime.subprocess.run")
    def test_execute_uses_linux_user_and_host_key_verification(self, run):
        run.return_value.returncode = 0
        run.return_value.stdout = "ok\n"
        run.return_value.stderr = ""
        result = self.runtime().execute(
            target="preview",
            command="id -un",
            linux_user="alice-agent",
        )
        argv = run.call_args.args[0]
        self.assertEqual(result["linux_user"], "alice-agent")
        self.assertIn("alice-agent@preview.example", argv)
        self.assertIn("StrictHostKeyChecking=yes", argv)
        self.assertIn("UserKnownHostsFile=/keys/known_hosts", argv)
        self.assertFalse(run.call_args.kwargs["shell"])

    @patch("ssh_runtime.subprocess.run")
    def test_write_file_sends_content_over_stdin(self, run):
        run.return_value.returncode = 0
        run.return_value.stdout = ""
        run.return_value.stderr = ""
        result = self.runtime().write_file(
            target="preview",
            path="/srv/alice/config.py",
            content="VALUE = 1\n",
            linux_user="alice-agent",
        )
        self.assertTrue(result["success"])
        self.assertEqual(run.call_args.kwargs["input"], "VALUE = 1\n")
        self.assertFalse(run.call_args.kwargs["shell"])
        self.assertIn("mktemp", run.call_args.args[0][-1])
        self.assertIn("mv -f", run.call_args.args[0][-1])

    @patch("ssh_runtime.subprocess.run")
    def test_timeout_is_reported(self, run):
        run.side_effect = subprocess.TimeoutExpired(["ssh"], 15)
        with self.assertRaises(SSHRuntimeError):
            self.runtime().execute(
                target="preview",
                command="sleep 60",
                linux_user="alice-agent",
            )

    def test_unknown_target_is_rejected(self):
        with self.assertRaises(SSHRuntimeError):
            self.runtime().execute(
                target="production",
                command="id",
                linux_user="alice-agent",
            )

    def test_trusted_identity_maps_to_linux_user(self):
        with patch("ssh_runtime.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = "alice-agent\\n"
            run.return_value.stderr = ""
            result = self.runtime().execute(
                target="preview",
                command="id -un",
                identity_id="owner-1",
            )
            self.assertEqual(result["linux_user"], "alice-agent")
            self.assertIn("alice-agent@preview.example", run.call_args.args[0])

    def test_unmapped_trusted_identity_is_rejected(self):
        with self.assertRaises(SSHRuntimeError):
            self.runtime().execute(
                target="preview",
                command="id -un",
                identity_id="owner-2",
            )

    def test_disallowed_linux_user_is_rejected(self):
        with self.assertRaises(SSHRuntimeError):
            self.runtime().execute(
                target="preview",
                command="id",
                linux_user="root",
            )

    def test_remote_paths_must_be_absolute(self):
        with self.assertRaises(SSHRuntimeError):
            self.runtime().read_file(
                target="preview",
                path="../config.py",
                linux_user="alice-agent",
            )

    def test_missing_known_hosts_is_rejected(self):
        runtime = SSHRuntime(
            {"preview": {"host": "preview.example", "default_user": "alice-agent"}}
        )
        with self.assertRaises(SSHRuntimeError):
            runtime.execute(target="preview", command="id -un")

    @patch("tests.test_ssh_runtime.subprocess.run")
    def test_runtime_tool_trace_redacts_command(self, run):
        from trace_manager import ExecutionTrace
        from universal_tool_platform import UniversalToolCall, UniversalToolExecutor
        from tool_registry import ToolRegistry
        from runtime_tools import RUNTIME_TOOLS

        registry = ToolRegistry()
        registry._tools["ssh_runtime_exec"] = RUNTIME_TOOLS["ssh_runtime_exec"]
        registry._categories["runtime"] = ["ssh_runtime_exec"]
        executor = UniversalToolExecutor(registry)
        trace = ExecutionTrace()
        call = UniversalToolCall(
            tool_name="ssh_runtime_exec",
            arguments={"target": "preview", "timeout_seconds": 10, "command": "echo secret"},
            transport="responses_api",
            call_id="call-1",
            user_id="owner-1",
        )
        run.return_value.returncode = 0
        run.return_value.stdout = "ok\\n"
        run.return_value.stderr = ""
        # Use the real executor, but point the runtime tool at a test-local runtime.
        import runtime_tools
        original = runtime_tools.runtime
        runtime_tools.runtime = self.runtime()
        try:
            result = executor.execute_with_trace(call, trace)
        finally:
            runtime_tools.runtime = original

        self.assertTrue(result["success"])
        self.assertEqual(trace.trace["tool_calls"][0]["arguments"]["command"], "<redacted>")
        self.assertEqual(trace.trace["tool_calls"][0]["result"]["stdout"], "<redacted>")
        self.assertEqual(
            [event["type"] for event in trace.trace["events"] if event["type"].startswith("runtime_")],
            ["runtime_started", "runtime_finished"],
        )

if __name__ == "__main__":
    unittest.main()
