"""Independent, offline RED->GREEN tests of the hardened Live Server candidate.

All Android communications are mocked. Nothing touches the production service,
user screen, clipboard, network authorization or private UI text.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch, Mock

# Repository tests must never read a real phone's credentials or files.
CAND = Path(__file__).resolve().parents[1] / "android" / "live_server"
sys.path.insert(0, str(CAND))
import types
from types import ModuleType


class SyntheticDelta:
    def compare(self, *_args):
        return {"changed": False, "sequence": 0}


fake_delta = ModuleType("android_delta")
fake_delta.DeltaDetector = SyntheticDelta
sys.modules["android_delta"] = fake_delta

# Importing the server creates its bearer token inside an isolated home only.
unit_home = tempfile.TemporaryDirectory(prefix="alice-live-ci-")
old_home = os.environ.get("HOME")
os.environ["HOME"] = unit_home.name


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, CAND / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


try:
    actions = load("review_candidate_actions", "android_actions.py")
    access = load("review_candidate_accessibility", "android_accessibility.py")
    live = load("review_candidate_live", "android_live_server.py")
finally:
    if old_home is None:
        os.environ.pop("HOME", None)
    else:
        os.environ["HOME"] = old_home
    unit_home.cleanup()

POLICY = {
    "model": "Redmi_9",
    "device": "galahad",
    "serial": "target:5555",
    "android_id": "0123456789abcdef",
}
TWO = """List of devices attached
other:5555 device product:test model:25062RN2DY device:creek transport_id:2
target:5555 device product:lineage_lava model:Redmi_9 device:galahad transport_id:1
"""
FAKE = """List of devices attached
replace:5555 device product:lineage_lava model:Redmi_9 device:galahad transport_id:3
"""
XML = b'<?xml version="1.0" encoding="UTF-8"?><hierarchy><node class="android.widget.TextView" text="PRIVATE_SYNTHETIC" bounds="[1,2][6,8]" clickable="false" /></hierarchy>'


def fake_adb(*cmd):
    if cmd == ("devices", "-l"):
        return TWO
    if cmd == ("-s", "target:5555", "shell", "settings", "get", "secure", "android_id"):
        return POLICY["android_id"]
    raise AssertionError(f"unexpected ADB probe: {cmd}")


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.file = Path(self.temp.name) / "policy.json"
        self.file.write_text(json.dumps(POLICY))
        guard = patch.object(actions, "POLICY", self.file)
        guard.start()
        self.addCleanup(guard.stop)

    def test_pinned_serial_and_android_id_accept_only_pair(self):
        with patch.object(actions, "adb", side_effect=fake_adb):
            self.assertEqual(actions.device(), "target:5555")

    def test_different_authorized_same_model_device_denied(self):
        with patch.object(actions, "adb", return_value=FAKE) as a:
            with self.assertRaisesRegex(RuntimeError, "transport"):
                actions.device()
            a.assert_called_once_with("devices", "-l")

    def test_same_serial_wrong_android_id_denied(self):
        with patch.object(
            actions,
            "adb",
            side_effect=lambda *x: TWO if x == ("devices", "-l") else "bbbbbbbbbbbbbbbb",
        ):
            with self.assertRaisesRegex(RuntimeError, "identity mismatch"):
                actions.device()

    def test_corrupt_identity_policy_fails_closed_before_adb(self):
        for mutation in ({"android_id": "garbage"}, {"serial": "new:3333"}, {"extra": "x"}):
            bad = dict(POLICY, **mutation)
            self.file.write_text(json.dumps(bad))
            with patch.object(actions, "adb") as a:
                if mutation.get("serial") == "new:3333":
                    with patch.object(actions, "adb", return_value=TWO):
                        with self.assertRaises(RuntimeError):
                            actions.device()
                    continue
                with self.assertRaises(RuntimeError):
                    actions.device()
                a.assert_not_called()

    def test_device_adapter_rejects_touch_on_keyguard(self):
        with (
            patch.object(actions, "device", return_value="target:5555"),
            patch.object(actions, "adb", return_value="isKeyguardShowing=true") as adb,
        ):
            with self.assertRaisesRegex(RuntimeError, "locked"):
                actions.action(types.SimpleNamespace(command="tap", x=5, y=6))
            adb.assert_called_once_with("-s", "target:5555", "shell", "dumpsys", "window")

    def test_lock_flag_true_false_unknown(self):
        self.assertTrue(actions.keyguard_state("foo isKeyguardShowing=true bar"))
        self.assertFalse(actions.keyguard_state("isKeyguardShowing=false"))
        self.assertTrue(actions.keyguard_state("isKeyguardShowing=false mShowingLockscreen=true"))
        self.assertTrue(actions.keyguard_state("no trusted keyguard field"))
        self.assertTrue(actions.keyguard_state(None))


class InterfaceTests(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self.handler = object.__new__(live.Handler)
        self.handler.send = lambda code, value: self.sent.append((code, value))

    def test_locked_focus_denied_even_when_target_valid(self):
        for focus in ("com.android.systemui/.keyguard.KeyguardView", "com.example/.MainActivity"):
            with patch.object(
                live,
                "execute",
                return_value={
                    "target": "Redmi_9",
                    "device": "target:5555",
                    "focus": focus,
                    "keyguard_locked": True,
                },
            ):
                self.assertFalse(self.handler.safe_screen())

    def test_unlocked_unrelated_app_allowed(self):
        with patch.object(
            live,
            "execute",
            return_value={
                "target": "Redmi_9",
                "device": "target:5555",
                "focus": "com.example.other/.MainActivity",
                "keyguard_locked": False,
            },
        ):
            self.assertTrue(self.handler.safe_screen())

    def post(self, body):
        raw = json.dumps(body).encode()
        self.handler.path = "/action"
        self.handler.headers = {"Content-Length": str(len(raw))}
        self.handler.rfile = io.BytesIO(raw)
        with patch.object(self.handler, "authorized", return_value=True):
            self.handler.do_POST()
        return self.sent[-1]

    def test_open_does_not_bypass_lock(self):
        with (
            patch.object(self.handler, "bound_status", return_value={"keyguard_locked": True}),
            patch.object(live, "execute") as e,
        ):
            status, data = self.post({"command": "open", "package": "com.example.app"})
            self.assertEqual(status, 423)
            self.assertEqual(data.get("error"), "screen_locked")
            e.assert_not_called()

    def test_wake_allowed_when_locked(self):
        with (
            patch.object(self.handler, "bound_status", return_value={"keyguard_locked": True}),
            patch.object(live, "execute", return_value={"submitted": True, "verified": False}),
        ):
            status, data = self.post({"command": "wake"})
            self.assertEqual(status, 200)
            self.assertFalse(data["verified"])

    def test_post_status_is_rejected_to_prevent_raw_focus_leak(self):
        with (
            patch.object(self.handler, "bound_status") as status,
            patch.object(live, "execute") as execute,
        ):
            code, body = self.post({"command": "status"})
            self.assertEqual(code, 400)
            self.assertEqual(body.get("error"), "invalid_request")
            status.assert_not_called()
            execute.assert_not_called()

    def test_anonymous_actions_do_not_execute(self):
        self.handler.path = "/action"
        self.handler.headers = {"Content-Length": "17"}
        self.handler.rfile = io.BytesIO(b'{"command":"wake"}')
        with (
            patch.object(self.handler, "authorized", return_value=False),
            patch.object(live, "execute") as e,
        ):
            self.handler.do_POST()
            e.assert_not_called()

    def test_dom_redacts_and_does_not_persist_text(self):
        self.handler.path = "/screen/dom"
        self.handler.headers = {}
        private = {
            "nodes": [
                {
                    "id": "ui0",
                    "parent_id": None,
                    "role": "text",
                    "text": "SYNTHETIC_PRIVATE_MARKER",
                    "bbox": [1, 2, 3, 4],
                }
            ]
        }
        with (
            patch.object(self.handler, "authorized", return_value=True),
            patch.object(self.handler, "safe_screen", return_value=True),
            patch.object(live, "execute", return_value={}),
            patch.object(live.DETECTOR, "compare", return_value={"changed": True, "sequence": 1}),
            patch.object(live, "build_ocr_dom", return_value=private),
        ):
            self.handler.do_GET()
        code, result = self.sent[-1]
        self.assertEqual(code, 200)
        self.assertNotIn("SYNTHETIC_PRIVATE_MARKER", json.dumps(result))
        self.assertNotIn("state", result)
        self.assertNotIn("diff", result)
        self.assertFalse(result["persistent"])
        self.assertTrue(result["tree"]["redacted"])

    def test_status_response_omits_raw_focus_and_serial(self):
        self.handler.path = "/status"
        with (
            patch.object(self.handler, "authorized", return_value=True),
            patch.object(
                self.handler,
                "bound_status",
                return_value={
                    "target": "Redmi_9",
                    "device": "fixture-private-serial",
                    "focus": "fixture-private-foreground",
                    "keyguard_locked": True,
                },
            ),
        ):
            self.handler.do_GET()
        status, data = self.sent[-1]
        self.assertEqual(status, 200)
        self.assertEqual(data["keyguard_locked"], True)
        self.assertNotIn("fixture-private-serial", json.dumps(data))
        self.assertNotIn("fixture-private-foreground", json.dumps(data))

    def test_no_global_adb_env_or_raw_ocr_state(self):
        s = (CAND / "android_live_server.py").read_text()
        self.assertNotIn('os.environ["ANDROID_SERIAL"]', s)
        self.assertNotIn("STATE_STORE.restore", s)
        self.assertNotIn("STATE_STORE.save", s)
        self.assertNotIn('"screen-dom.json"', s)
        self.assertNotIn("com.mobile.legends", s)


class AccessibilityTests(unittest.TestCase):
    def test_explicit_serial_and_cleanup_on_success(self):
        calls = []

        def runner(args, **kwargs):
            calls.append(args)
            if "exec-out" in args:
                return subprocess.CompletedProcess(args, 0, stdout=XML)
            return subprocess.CompletedProcess(args, 0, stdout=b"")

        with patch.object(access.subprocess, "run", side_effect=runner):
            parsed = access.read_accessibility("target:5555")
        self.assertEqual(parsed["count"], 1)
        self.assertTrue(all(x[:3] == ["adb", "-s", "target:5555"] for x in calls))
        self.assertTrue(any("rm" in x for x in calls))
        self.assertNotIn("/sdcard/", str(calls))
        self.assertEqual(calls[-1][3:5], ["shell", "rm"])

    def test_xml_parse_error_still_cleans_up(self):
        calls = []

        def runner(args, **kwargs):
            calls.append(args)
            if "exec-out" in args:
                return subprocess.CompletedProcess(args, 0, stdout=b"<broken")
            return subprocess.CompletedProcess(args, 0, stdout=b"")

        with patch.object(access.subprocess, "run", side_effect=runner):
            with self.assertRaises(Exception):
                access.read_accessibility("target:5555")
        self.assertEqual(calls[-1][4], "rm")


if __name__ == "__main__":
    unittest.main(verbosity=2)
