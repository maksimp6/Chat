import os
import socket
import struct
import tempfile
import threading
import unittest

class UnixPeerTests(unittest.TestCase):
    def test_peer_credentials(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "control.sock")
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server.bind(path)
            os.chmod(path, 0o600)
            server.listen(1)
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                client.connect(path)
                accepted, _ = server.accept()
                try:
                    raw = accepted.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
                    pid, uid, gid = struct.unpack("3i", raw)
                    self.assertEqual(uid, os.getuid())
                    self.assertEqual(gid, os.getgid())
                    self.assertGreater(pid, 0)
                    self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
                finally:
                    accepted.close()
            finally:
                client.close()
                server.close()

    def test_socket_not_tcp(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "control.sock")
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                server.bind(path)
                self.assertTrue(os.path.exists(path))
                self.assertEqual(server.family, socket.AF_UNIX)
            finally:
                server.close()

if __name__ == "__main__":
    unittest.main()
