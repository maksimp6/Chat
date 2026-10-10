import os
import secrets
import tempfile
import unittest
from pathlib import Path
from alice_mouse_live_role_adapter import LiveRoleAuthority


class LiveRoleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "controller-token"
        self.viewer = secrets.token_urlsafe(32)
        self.controller = secrets.token_urlsafe(32)
        self.a = LiveRoleAuthority(self.viewer, self.path)

    def test_existing_live_bearer_is_viewer_only(self):
        self.assertEqual(self.a.authenticate("Bearer " + self.viewer), "viewer")
        self.assertFalse(self.a.authorize_command("Bearer " + self.viewer, {"action": "down"}))

    def test_no_controller_provisioned_fail_closed(self):
        self.assertFalse(self.a.authorize_command("Bearer " + self.controller, {"action": "down"}))

    def test_server_owned_controller(self):
        self.path.write_text(self.controller)
        self.path.chmod(0o600)
        self.assertTrue(self.a.authorize_command("Bearer " + self.controller, {"action": "down"}))

    def test_client_role_spoof_denied(self):
        self.path.write_text(self.controller)
        self.path.chmod(0o600)
        for k in ("role", "principal", "admin", "is_controller"):
            with self.subTest(k=k):
                self.assertFalse(
                    self.a.authorize_command(
                        "Bearer " + self.controller, {"action": "down", k: "controller"}
                    )
                )

    def test_viewer_role_spoof_denied(self):
        self.assertFalse(self.a.authorize_command("Bearer " + self.viewer, {"role": "controller"}))

    def test_insecure_token_file_denied(self):
        self.path.write_text(self.controller)
        self.path.chmod(0o644)
        self.assertFalse(self.a.authorize_command("Bearer " + self.controller, {"action": "down"}))

    def test_invalid_token_denied(self):
        self.assertIsNone(self.a.authenticate("Bearer wrong"))

    def test_missing_bearer_denied(self):
        self.assertIsNone(self.a.authenticate("wrong"))

    def test_symlink_controller_denied(self):
        target = self.path.with_name("token-real")
        target.write_text(self.controller)
        target.chmod(0o600)
        self.path.symlink_to(target)
        self.assertFalse(self.a.authorize_command("Bearer " + self.controller, {"action": "move"}))

    def test_controller_token_directory_denied(self):
        self.path.mkdir()
        self.assertFalse(self.a.authorize_command("Bearer " + self.controller, {"action": "move"}))

    def test_explicit_controller_revocation(self):
        self.path.write_text(self.controller)
        self.path.chmod(0o600)
        self.assertTrue(self.a.authorize_command("Bearer " + self.controller, {"action": "move"}))
        self.path.unlink()
        self.assertFalse(self.a.authorize_command("Bearer " + self.controller, {"action": "move"}))


if __name__ == "__main__":
    unittest.main()
