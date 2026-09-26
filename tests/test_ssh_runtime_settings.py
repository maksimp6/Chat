import unittest
from unittest.mock import patch

from ssh_runtime import SSHRuntime, SSHRuntimeError
from ssh_runtime_settings import validate_settings


class SSHRuntimeSettingsTests(unittest.TestCase):
    BASE = {
        "enabled": True,
        "read_only": False,
        "allow_command_execution": True,
        "allow_write_operations": True,
        "max_output_bytes": 8192,
        "known_hosts": "/srv/alice/ssh/known_hosts",
        "command_allowlist": [r"id(?:\s+-un)?", r"cat(?:\s+--)?\s+.*", r"sudo\s+.*"],
        "allow_privileged_operations": False,
        "approval_required_for_write": True,
        "approval_required_for_privileged": True,
        "targets": {
            "preview": {
                "host": "preview.example",
                "port": 22,
                "default_user": "alice-agent",
                "allowed_users": ["alice-agent"],
                "identity_map": {"owner-1": "alice-agent"},
                "workspace_root": "/srv/alice",
                "identity_file": "/srv/alice/keys/preview",
                "connect_timeout_seconds": 5,
                "command_timeout_seconds": 30,
                "max_output_bytes": 8192,
            }
        },
    }

    def test_command_allowlist_is_required_when_execution_is_enabled(self):
        settings = dict(self.BASE)
        settings["command_allowlist"] = []
        with self.assertRaises(SSHRuntimeError):
            validate_settings(settings)

    def test_invalid_command_allowlist_pattern_is_rejected(self):
        settings = dict(self.BASE)
        settings["command_allowlist"] = ["["]
        with self.assertRaises(SSHRuntimeError):
            validate_settings(settings)

    def test_settings_update_is_recorded_in_execution_trace(self):
        from ssh_runtime_settings import save_settings

        trace = type("Trace", (), {"events": []})()
        trace.add_event = lambda event_type, payload: trace.events.append((event_type, payload))
        with (
            patch("ssh_runtime_settings.get_current_trace", return_value=trace),
            patch("ssh_runtime_settings.set_config"),
        ):
            save_settings(self.BASE)
        self.assertEqual(trace.events[0][0], "ssh_runtime_settings_updated")
        self.assertEqual(trace.events[0][1]["targets"], ["preview"])
        self.assertEqual(trace.events[0][1]["command_allowlist_count"], 3)

    def test_privileged_operations_are_disabled_by_default(self):
        from ssh_runtime_settings import assert_operation_allowed

        with patch("ssh_runtime_settings.get_settings", return_value=self.BASE):
            with self.assertRaises(SSHRuntimeError):
                assert_operation_allowed("execute", command="sudo id", approved=True)

    def test_write_requires_approval(self):
        from ssh_runtime_settings import assert_operation_allowed

        with patch("ssh_runtime_settings.get_settings", return_value=self.BASE):
            with self.assertRaises(SSHRuntimeError):
                assert_operation_allowed("write_file", approved=False)

    def test_valid_settings_normalize_read_only_policy(self):
        settings = dict(self.BASE)
        settings["read_only"] = True
        settings["allow_command_execution"] = True
        settings["allow_write_operations"] = True

        normalized = validate_settings(settings)

        self.assertFalse(normalized["allow_command_execution"])
        self.assertFalse(normalized["allow_write_operations"])

    def test_enabled_runtime_requires_a_target(self):
        settings = dict(self.BASE)
        settings["targets"] = {}
        with self.assertRaises(SSHRuntimeError):
            validate_settings(settings)

    def test_enabled_runtime_requires_known_hosts(self):
        settings = dict(self.BASE)
        settings["known_hosts"] = None
        settings["targets"] = {
            "preview": {
                **self.BASE["targets"]["preview"],
                "known_hosts": None,
            }
        }
        with self.assertRaises(SSHRuntimeError):
            validate_settings(settings)

    def test_output_limit_has_safe_bounds(self):
        settings = dict(self.BASE)
        settings["max_output_bytes"] = 1024
        with self.assertRaises(SSHRuntimeError):
            validate_settings(settings)

        settings["max_output_bytes"] = 11 * 1024 * 1024
        with self.assertRaises(SSHRuntimeError):
            validate_settings(settings)

    def test_target_output_limit_truncates_command_result(self):
        runtime = SSHRuntime(self.BASE["targets"], known_hosts=self.BASE["known_hosts"])
        with patch("ssh_runtime.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = "x" * 10000
            run.return_value.stderr = ""
            result = runtime.execute(
                target="preview",
                command="printf x",
                identity_id="owner-1",
            )
        self.assertLessEqual(len(result["stdout"].encode()), 8192)
        self.assertTrue(result["stdout"].endswith("...[output truncated]"))

    @patch("ssh_runtime_settings.get_config")
    @patch("ssh_runtime_settings.set_config")
    def test_build_runtime_applies_global_output_limit(self, set_config, get_config):
        from ssh_runtime_settings import build_runtime

        stored = dict(self.BASE)
        stored["max_output_bytes"] = 16384
        stored["targets"] = {"preview": {**self.BASE["targets"]["preview"]}}
        stored["targets"]["preview"].pop("max_output_bytes")
        get_config.side_effect = lambda key, default=None: (
            stored if key == "ssh_runtime_settings" else default
        )

        runtime = build_runtime()
        self.assertEqual(runtime._targets["preview"].max_output_bytes, 16384)

    @patch("ssh_runtime_settings.get_config")
    @patch("ssh_runtime_settings.set_config")
    @patch("ssh_runtime_settings.subprocess.run")
    def test_connection_test_uses_only_fixed_true(self, run, set_config, get_config):
        from ssh_runtime_settings import test_connection

        stored = dict(self.BASE)
        get_config.side_effect = lambda key, default=None: (
            stored if key == "ssh_runtime_settings" else default
        )
        run.return_value.returncode = 0
        run.return_value.stdout = ""
        run.return_value.stderr = ""

        result = test_connection("preview", "owner-1")

        self.assertTrue(result["success"])
        argv = run.call_args.args[0]
        self.assertEqual(argv[-1], "true")
        self.assertNotIn("echo", argv[-1])

    @patch("ssh_runtime_settings.get_config")
    @patch("ssh_runtime_settings.set_config")
    def test_saved_settings_are_used(self, set_config, get_config):
        from ssh_runtime_settings import get_settings, save_settings

        saved = {}

        def read(key, default=None):
            return saved.get(key, default)

        def write(key, value):
            saved[key] = value

        get_config.side_effect = read
        set_config.side_effect = write
        settings = save_settings(self.BASE)
        self.assertEqual(settings["targets"]["preview"]["host"], "preview.example")
        self.assertEqual(get_settings()["targets"]["preview"]["default_user"], "alice-agent")


if __name__ == "__main__":
    unittest.main()
