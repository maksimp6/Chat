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
    def test_saved_settings_are_used(self, set_config, get_config):
        from ssh_runtime_settings import get_settings, save_settings

        get_config.return_value = None
        settings = save_settings(self.BASE)
        set_config.assert_called_once()
        self.assertEqual(settings["targets"]["preview"]["host"], "preview.example")
        self.assertEqual(get_settings()["targets"]["preview"]["default_user"], "alice-agent")


if __name__ == "__main__":
    unittest.main()
