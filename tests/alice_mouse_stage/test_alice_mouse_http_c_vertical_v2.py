import http.client
import json
import secrets
import tempfile
import threading
import unittest
from pathlib import Path

from alice_mouse_http_c_vertical_v2 import CVerifier, Server


class VerticalHTTPCTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="alice-http-c-v2-")
        self.addCleanup(self.temp.cleanup)
        self.controller = secrets.token_urlsafe(32)
        self.viewer = secrets.token_urlsafe(32)
        controller_path = Path(self.temp.name) / "controller"
        controller_path.write_text(self.controller)
        controller_path.chmod(0o600)
        self.controller_path = controller_path
        self.c = CVerifier()
        self.server = Server(self.viewer, controller_path, self.c)
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.01}
        )
        self.thread.start()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.assertFalse(self.thread.is_alive())
        self.c.close()

    def post(self, token, endpoint, body):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=2)
        conn.request(
            "POST",
            endpoint,
            json.dumps(body),
            {"Authorization": "Bearer " + token, "Content-Type": "application/json"},
        )
        result = conn.getresponse()
        status, payload = result.status, json.loads(result.read())
        conn.close()
        return status, payload["result"]

    def begin(self):
        self.assertEqual(self.post(self.controller, "/mouse/start", {})[0], 200)

    def action(self, seq=1):
        return {"action": 1, "seq": seq, "x": 8, "y": -2, "button": 1}

    def test_controller_issues_session_then_event(self):
        self.begin()
        status, value = self.post(self.controller, "/mouse/command", self.action())
        self.assertEqual(status, 200)
        self.assertEqual(value, "EVENT 1 1 8 -2")

    def test_session_not_created_without_auth(self):
        self.assertEqual(self.post(self.viewer, "/mouse/start", {})[0], 403)
        self.assertEqual(self.post(self.controller, "/mouse/command", self.action())[0], 403)

    def test_viewer_cannot_act_after_start(self):
        self.begin()
        self.assertEqual(self.post(self.viewer, "/mouse/command", self.action())[0], 403)

    def test_replayed_sequence_rejected_by_c(self):
        self.begin()
        self.assertEqual(self.post(self.controller, "/mouse/command", self.action())[0], 200)
        self.assertEqual(self.post(self.controller, "/mouse/command", self.action())[0], 403)

    def test_incrementing_sequence(self):
        self.begin()
        self.assertEqual(self.post(self.controller, "/mouse/command", self.action(1))[0], 200)
        self.assertEqual(self.post(self.controller, "/mouse/command", self.action(2))[0], 200)

    def test_spoofed_role_and_principal(self):
        for field in ("role", "principal", "admin", "is_controller"):
            with self.subTest(field=field):
                self.assertEqual(
                    self.post(self.controller, "/mouse/start", {field: "controller"})[0], 403
                )

    def test_bad_token_denied(self):
        self.assertEqual(self.post("wrong", "/mouse/start", {})[0], 403)

    def test_revoked_controller_denied(self):
        self.begin()
        self.controller_path.unlink()
        self.assertEqual(self.post(self.controller, "/mouse/command", self.action())[0], 403)

    def test_out_of_range_denied(self):
        self.begin()
        self.assertEqual(
            self.post(self.controller, "/mouse/command", {**self.action(), "x": 501})[0], 400
        )

    def test_verifier_exit_fails_closed(self):
        self.begin()
        self.c.process.terminate()
        self.c.process.wait(timeout=2)
        self.assertEqual(self.post(self.controller, "/mouse/command", self.action())[0], 503)

    def test_c_sign_verb_unavailable(self):
        self.assertEqual(self.c.call("SIGN 1 8 2"), "DENIED")

    def test_c_tampered_grant_denied(self):
        self.begin()
        grant = self.c.build_grant(1, 1, 8, -2, 1)
        modified = grant[:-2] + ("00" if grant[-2:] != "00" else "ff")
        self.assertEqual(self.c.call("VERIFY " + modified), "DENIED")

    def test_reissue_revokes_previous_session(self):
        self.begin()
        old = self.c.build_grant(1, 1, 8, -2, 1)
        self.begin()
        self.assertEqual(self.c.call("VERIFY " + old), "DENIED")
        self.assertEqual(self.post(self.controller, "/mouse/command", self.action())[0], 200)

    def test_unknown_session_even_with_valid_mac(self):
        import struct
        import hmac

        self.begin()
        original = bytearray.fromhex(self.c.build_grant(1, 1, 8, -2, 1))
        struct.pack_into("<Q", original, 12, self.c.session_id ^ 1)
        raw = bytes(original[:-32])
        new = (raw + hmac.digest(self.c.key, raw, "sha256")).hex()
        self.assertEqual(self.c.call("VERIFY " + new), "DENIED")

    def test_expired_grant_even_when_signed(self):
        import struct
        import hmac

        self.begin()
        original = bytearray.fromhex(self.c.build_grant(1, 1, 8, -2, 1))
        issued_offset = struct.calcsize("<IIIQBBBBhh")
        struct.pack_into(
            "<Q",
            original,
            issued_offset,
            struct.unpack_from("<Q", original, issued_offset)[0] - 1000,
        )
        raw = bytes(original[:-32])
        new = (raw + hmac.digest(self.c.key, raw, "sha256")).hex()
        self.assertEqual(self.c.call("VERIFY " + new), "DENIED")

    def test_parallel_same_sequence_one_accept(self):
        import concurrent.futures

        self.begin()
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as workers:
            results = list(
                workers.map(
                    lambda _: self.post(self.controller, "/mouse/command", self.action())[0],
                    range(6),
                )
            )
        self.assertEqual(results.count(200), 1)
        self.assertEqual(results.count(403), 5)

    def test_c_rejects_malformed_packet(self):
        self.begin()
        for packet in ("VERIFY xyz", "VERIFY 00", "SIGN 2 3 4"):
            self.assertEqual(self.c.call(packet), "DENIED")

    def test_c_duplicate_identical_grant(self):
        self.begin()
        grant = self.c.build_grant(1, 1, 8, -2, 1)
        self.assertTrue(self.c.call("VERIFY " + grant).startswith("EVENT "))
        self.assertEqual(self.c.call("VERIFY " + grant), "DENIED")


if __name__ == "__main__":
    unittest.main()
