"""Offline tests of Redmi 9 Live Server target binding and authorization.

All ADB/input calls are mocked; no device interaction or token printing.
"""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))


def import_local(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


actions = import_local("alice_actions_test", BASE / "android_actions.py")
server = import_local("alice_live_server_test", BASE / "android_live_server.py")

TWO = """List of devices attached
192.168.31.109:42125 device product:creek_n_ru model:25062RN2DY device:creek transport_id:3
192.168.31.29:35087 device product:lineage_lava model:Redmi_9 device:galahad transport_id:1
"""
OTHER = """List of devices attached
192.168.31.109:42125 device product:creek_n_ru model:25062RN2DY device:creek transport_id:3
"""
DUPLICATE = """List of devices attached
one device model:Redmi_9 device:galahad transport_id:1
two device model:Redmi_9 device:galahad transport_id:2
"""
OFFLINE = """List of devices attached
one offline model:Redmi_9 device:galahad
"""


class DeviceSelection(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="alice-live-unit-")
        self.addCleanup(self.temp.cleanup)
        policy = Path(self.temp.name) / "target.json"
        policy.write_text(json.dumps({"model":"Redmi_9","device":"galahad"}))
        self.policy_patch = patch.object(actions, "POLICY", policy)
        self.policy_patch.start()
        self.addCleanup(self.policy_patch.stop)

    def test_selects_redmi9_not_first_adb_phone(self):
        with patch.object(actions, "adb", return_value=TWO) as a:
            self.assertEqual(actions.device(), "192.168.31.29:35087")
            a.assert_called_once_with("devices", "-l")

    def test_offline_and_wrong_target_fail_closed(self):
        for data in (OTHER, OFFLINE, DUPLICATE, "List of devices attached\n"):
            with self.subTest(output=data), patch.object(actions, "adb", return_value=data):
                with self.assertRaisesRegex(RuntimeError, "unavailable or ambiguous"):
                    actions.device()

    def test_tampered_policy_rejected_without_device_use(self):
        actions.POLICY.write_text(json.dumps({"model":"25062RN2DY","device":"creek"}))
        with patch.object(actions, "adb") as a:
            with self.assertRaisesRegex(RuntimeError, "Unexpected device policy"):
                actions.device()
            a.assert_not_called()

    def test_wake_uses_only_fixed_keycode_on_pinned_target(self):
        with patch.object(actions, "device", return_value="redmi9-device"), patch.object(actions, "adb") as a:
            result = actions.action(SimpleNamespace(command="wake"))
            self.assertEqual(result["command"], "wake")
            self.assertFalse(result["verified"])
            a.assert_called_once_with("-s","redmi9-device","shell","input","keyevent","KEYCODE_WAKEUP")


class ServerGuards(unittest.TestCase):
    def test_live_screen_accepts_any_app_if_device_is_pinned(self):
        h = object.__new__(server.Handler)
        status = {"target": "Redmi_9", "device":"redmi9-device", "focus":"other.package/MainActivity"}
        previous = os.environ.get("ANDROID_SERIAL")
        try:
            with patch.object(server, "execute", return_value=status):
                self.assertTrue(h.safe_screen())
                self.assertEqual(os.environ.get("ANDROID_SERIAL"), "redmi9-device")
        finally:
            if previous is None:
                os.environ.pop("ANDROID_SERIAL", None)
            else:
                os.environ["ANDROID_SERIAL"] = previous

    def test_wrong_or_missing_device_fails_closed(self):
        h = object.__new__(server.Handler)
        for status in (
            {"target":"25062RN2DY","device":"wrong","focus":"app"},
            {"target":"Redmi_9","device":"","focus":"app"},
            {"target":"Redmi_9","device":"any","focus":None},
        ):
            with self.subTest(status=status), patch.object(server, "execute", return_value=status):
                self.assertFalse(h.safe_screen())

    def test_no_game_allowlist_or_arbitrary_command(self):
        content = (BASE/"android_live_server.py").read_text()
        self.assertNotIn("SAFE_APP", content)
        self.assertNotIn("com.mobile.legends", content)
        self.assertIn('ThreadingHTTPServer(("127.0.0.1", 8765)', content)
        self.assertNotIn('"shell"', server.ALLOWED)
        self.assertNotIn('"power"', server.ALLOWED)
        self.assertTrue({"tap","swipe","open","wake","screenshot"}.issubset(server.ALLOWED))


if __name__ == "__main__":
    unittest.main(verbosity=2)
