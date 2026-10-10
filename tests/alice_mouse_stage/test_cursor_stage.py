"""Stage-only cursor tests, including authenticated HTTP->C->visual sink."""
import http.client
import importlib.util
import json
from pathlib import Path
import secrets
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path.home()))

from cursor_stage import CursorPreviewAdapter
from alice_mouse_http_c_vertical_v2 import CVerifier, Server


class CursorStageTests(unittest.TestCase):
    def setUp(self):
        self.shown = []
        self.hidden = []
        self.cursor = CursorPreviewAdapter(
            2340, 1080,
            show=lambda x, y: self.shown.append((x, y)),
            hide=lambda: self.hidden.append("hide"),
        )

    def test_valid_move_and_clamped_edges(self):
        self.assertTrue(self.cursor.consume("EVENT 1 1 500 500"))
        self.assertEqual(self.shown[-1], (1670, 1040))
        self.assertTrue(self.cursor.consume("EVENT 1 1 500 500"))
        self.assertEqual(self.shown[-1], (2170, 1079))
        self.assertTrue(self.cursor.consume("EVENT 1 1 500 500"))
        self.assertEqual(self.shown[-1], (2339, 1079))

    def test_unsigned_or_invalid_event_never_drawn(self):
        for raw in ("move 50 50", "EVENT 1 1 501 1", "EVENT 1 1 1 1\nmove 20 20",
                    "EVENT 1 3 4 5", "EVENT 1 1 4 5; whoami", ""):
            with self.subTest(raw=raw):
                self.assertFalse(self.cursor.consume(raw))
        self.assertEqual(self.shown, [])

    def test_drag_and_safety_disconnect(self):
        self.assertTrue(self.cursor.consume("EVENT 2 1 0 0"))
        self.assertFalse(self.cursor.consume("EVENT 2 2 0 0"))
        self.assertFalse(self.cursor.consume("EVENT 3 2 0 0"))
        self.assertTrue(self.cursor.consume("EVENT 3 1 0 0"))
        self.cursor.disconnect()
        self.assertEqual(self.hidden, ["hide"])
        self.assertIsNone(self.cursor.state.button)

    def test_rotation_invalidates_preview(self):
        self.assertTrue(self.cursor.consume("EVENT 1 1 10 10"))
        self.assertTrue(self.cursor.display_changed(1080, 2340, 1))
        self.assertEqual(len(self.hidden), 1)
        self.assertEqual((self.cursor.state.x, self.cursor.state.y), (540, 1170))
        self.assertFalse(self.cursor.display_changed(12, 2340, 1))

    def test_android_overlay_is_visual_only(self):
        source = (ROOT / "PetActivity.java").read_text()
        self.assertIn("class CursorPreviewView extends View", source)
        self.assertIn("FLAG_NOT_TOUCHABLE", source)
        self.assertIn("FLAG_NOT_FOCUSABLE", source)
        self.assertIn("cursorTimer.postDelayed(cursorTimeout,3000L)", source)
        self.assertIn("hideCursor();if(probeView", source)

    def test_staged_cli_only_uses_am_start(self):
        spec = importlib.util.spec_from_file_location("staged_pet_cli", ROOT / "pet.py")
        pet = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(pet)
        commands = []
        with patch.object(pet, "adb", side_effect=lambda *a: commands.append(a) or "OK"), \
             patch("sys.argv", ["pet.py", "cursor_preview", "--x", "123", "--y", "456"]):
            self.assertEqual(pet.main(), 0)
        self.assertEqual(commands, [
            ("shell", "am", "start", "-n", "org.alice.pet/.PetActivity",
             "--es", "pet_action", "cursor_preview", "--ei", "x", "123",
             "--ei", "y", "456")
        ])


class AuthenticatedPreviewE2E(unittest.TestCase):
    def test_http_c_verifier_to_visual_preview_and_replay_denial(self):
        with tempfile.TemporaryDirectory(prefix="mouse-preview-e2e-") as tmp:
            controller = secrets.token_urlsafe(32)
            viewer = secrets.token_urlsafe(32)
            secret = Path(tmp) / "controller"
            secret.write_text(controller)
            secret.chmod(0o600)
            verifier = CVerifier()
            server = Server(viewer, secret, verifier)
            worker = threading.Thread(target=server.serve_forever,
                                      kwargs={"poll_interval": 0.01})
            worker.start()
            shown = []
            visual = CursorPreviewAdapter(2340, 1080,
                                           show=lambda x, y: shown.append((x, y)),
                                           hide=lambda: None)

            def request(token, route, data):
                conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=2)
                conn.request("POST", route, json.dumps(data), {
                    "Authorization": "Bearer " + token,
                    "Content-Type": "application/json",
                })
                response = conn.getresponse()
                result = response.status, json.loads(response.read())["result"]
                conn.close()
                return result

            try:
                action = {"action": 1, "seq": 1, "x": 8, "y": -2, "button": 1}
                self.assertEqual(request(viewer, "/mouse/start", {})[0], 403)
                self.assertEqual(request(controller, "/mouse/start", {})[0], 200)
                self.assertEqual(request(viewer, "/mouse/command", action)[0], 403)
                status, event = request(controller, "/mouse/command", action)
                self.assertEqual(status, 200)
                self.assertTrue(visual.consume(event))
                self.assertEqual(shown, [(1178, 538)])
                self.assertEqual(request(controller, "/mouse/command", action)[0], 403)
                self.assertEqual(shown, [(1178, 538)])
            finally:
                server.shutdown()
                server.server_close()
                worker.join(2)
                verifier.close()
                self.assertFalse(worker.is_alive())


if __name__ == "__main__":
    unittest.main()
