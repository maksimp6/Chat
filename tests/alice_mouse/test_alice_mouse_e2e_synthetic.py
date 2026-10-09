import json
import os
import socket
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from typing import ClassVar

from alice_mouse_bridge import MouseBridge
from alice_mouse_http_stage import MouseHandler


class EndToEndTests(unittest.TestCase):
    def test_authenticated_http_to_unix(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "mouse.sock")
            backend = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            backend.bind(path)
            backend.listen(1)
            captured = []

            def serve():
                conn, _ = backend.accept()
                with conn:
                    captured.append(conn.recv(80))
                    conn.sendall(b"OK\n")

            worker = threading.Thread(target=serve, daemon=True)
            worker.start()

            class TestHandler(MouseHandler):
                bridge = MouseBridge(path, {"org.example.safe"})
                foreground = "org.example.safe"
                token_roles: ClassVar[dict[str, str]] = {
                    "synthetic-controller-key": "controller",
                    "synthetic-viewer-key": "viewer",
                }

            http = ThreadingHTTPServer(("127.0.0.1", 0), TestHandler)
            thread = threading.Thread(
                target=http.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
            )
            thread.start()
            try:
                base = "http://127.0.0.1:" + str(http.server_address[1]) + "/mouse"

                def request(token, action):
                    body = json.dumps({"action": action, "x": 7, "y": -3}).encode()
                    req = urllib.request.Request(
                        base, body, {"Authorization": "Bearer " + token}, method="POST"
                    )
                    try:
                        with urllib.request.urlopen(req, timeout=2) as response:
                            return response.status, json.load(response)
                    except urllib.error.HTTPError as error:
                        try:
                            return error.code, json.load(error)
                        finally:
                            error.close()

                self.assertEqual(request("synthetic-viewer-key", "click")[0], 403)
                self.assertEqual(request("bad-key", "click")[0], 401)
                self.assertEqual(captured, [])
                status, result = request("synthetic-controller-key", "move")
                self.assertEqual(status, 200)
                self.assertTrue(result["ok"])
                worker.join(timeout=2)
                self.assertEqual(captured, [b"move 7 -3\n"])
            finally:
                http.shutdown()
                http.server_close()
                thread.join(timeout=2)
                backend.close()


if __name__ == "__main__":
    unittest.main()
