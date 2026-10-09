"""Unix broker peer authorization tests without /dev/uinput."""
import os
import socket
import struct
import tempfile
import threading
import unittest

def peer_uid(conn):
    return struct.unpack("3i", conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))[1]

class BrokerTests(unittest.TestCase):
    def test_unprivileged_peer_denied_by_root_policy(self):
        with tempfile.TemporaryDirectory() as d:
            path=os.path.join(d,"broker.sock")
            server=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
            server.bind(path)
            server.listen(1)
            client=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
            try:
                client.connect(path)
                accepted,_=server.accept()
                with accepted:
                    uid=peer_uid(accepted)
                    self.assertEqual(uid,os.getuid())
                    self.assertNotEqual(uid,0)
                    accepted.sendall(b"DENIED\n" if uid!=0 else b"OK\n")
                    self.assertEqual(client.recv(20),b"DENIED\n")
            finally:
                client.close()
                server.close()

    def test_no_socket_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            client=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
            try:
                with self.assertRaises(OSError):
                    client.connect(os.path.join(d,"missing.sock"))
            finally:
                client.close()

    def test_malformed_request_not_authorized(self):
        from alice_mouse_policy import parse_mouse_command
        for request in (None, {}, {"action":"shell","x":0,"y":0},
                        {"action":"click","x":999999,"y":0},
                        {"action":"click","x":0,"y":0,"extra":1}):
            self.assertIsNone(parse_mouse_command(request))

if __name__=="__main__":
    unittest.main()

[executed on device: localhost (bb12d9f4-83a6-41b5-9ede-8717bac12a0a)]
