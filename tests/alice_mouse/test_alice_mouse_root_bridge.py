"""Policy/security tests for the UID-verified Alice Mouse root broker."""
import os
import pathlib
import socket
import unittest
from unittest import mock
from alice_mouse_root_bridge import decode_command, focus_is_safe, identity, ensure_private_dir
from alice_mouse_bridge import MouseBridge

class RootBrokerTests(unittest.TestCase):
    def test_bounded_allowed_mouse_ops(self):
        self.assertEqual(decode_command(b"move -500 500\n"), ("move", -500, 500))
        self.assertEqual(decode_command(b"click 0 0\n"), ("click", 0, 0))
    def test_refuse_unknown_commands_and_injection(self):
        for raw in (
            b"shell 0 0\n", b"scroll 0 0\n", b"click 501 0\n",
            b"click 1 -501\n", b"click 0 0; reboot\n",
            b"click 0 0\nmove 1 1\n", b"click 0 0\x00",
            b"click 3.2 5\n", b"click 0 0\n"*40
        ):
            with self.subTest(raw=raw):
                self.assertIsNone(decode_command(raw))
    def test_credential_is_real_kernel_peer_uid(self):
        a, b = socket.socketpair(socket.AF_UNIX)
        with a, b:
            pid, uid, gid = identity(a)
            self.assertEqual(uid, os.getuid())
            self.assertEqual(pid, os.getpid())
    def test_test_browser_required_fail_closed(self):
        with mock.patch("alice_mouse_root_bridge.subprocess.run") as run:
            run.return_value.stdout = "mCurrentFocus=Window{2 u0 com.android.settings/.Settings}\n"
            self.assertFalse(focus_is_safe())
            run.return_value.stdout = "mCurrentFocus=Window{2 u0 com.yandex.browser/com.yandex.browser.YandexBrowserMainActivity}\n"
            self.assertTrue(focus_is_safe())
            run.side_effect = TimeoutError()
            self.assertFalse(focus_is_safe())
    def test_private_dir_validated(self):
        with self.assertRaises(PermissionError):
            ensure_private_dir(0)
        ensure_private_dir(os.getuid())
    def test_role_gate_viewer_denied_before_root(self):
        bridge=MouseBridge("/nonexistent/alice-mouse.sock",{"com.yandex.browser"})
        got=bridge.handle("viewer",{"action":"click","x":0,"y":0},"com.yandex.browser")
        self.assertEqual(got["error"],"role_denied")
        got=bridge.handle("controller",{"action":"click","x":0,"y":0},"com.android.settings")
        self.assertEqual(got["error"],"app_denied")
        got=bridge.handle("controller",{"action":"click","x":0,"y":0},"com.yandex.browser",sensitive=True)
        self.assertEqual(got["error"],"confirmation_required")
        got=bridge.handle("controller",{"action":"keyboard","x":0,"y":0},"com.yandex.browser")
        self.assertEqual(got["error"],"invalid_command")
if __name__=="__main__":
    unittest.main()

[executed on device: localhost (bb12d9f4-83a6-41b5-9ede-8717bac12a0a)]
