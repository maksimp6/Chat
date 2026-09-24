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


if __name__ == "__main__":
    unittest.main()
