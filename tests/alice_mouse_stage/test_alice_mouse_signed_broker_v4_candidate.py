"""Isolated SignedBroker v4 negative and concurrency tests; no root/uinput."""
import base64
import concurrent.futures
import hmac
import json
import os
import secrets
import socket
import tempfile
import threading
import unittest
from pathlib import Path

from alice_mouse_signed_broker_v4_candidate import SignedBroker, serve


class SignedBrokerV4Tests(unittest.TestCase):
    def setUp(self):
        self.now = 10_000_000_000
        self.key = secrets.token_bytes(32)
        self.delivered = []
        self.broker = SignedBroker(self.key, os.getuid(), clock=lambda: self.now,
                                   forward=lambda g: self.delivered.append(g) or True)

    def packet(self, **update):
        grant = {"role": "controller", "action": "move", "seq": 1,
                 "nonce": secrets.token_hex(16), "until": self.now + 100_000_000,
                 "epoch": self.broker.epoch, "x": 8, "y": 0, "button": 1}
        grant.update(update)
        raw = json.dumps(grant, sort_keys=True, separators=(",", ":")).encode()
        mac = hmac.digest(self.key, raw, "sha256").hex()
        return json.dumps({"grant": base64.b64encode(raw).decode(), "sig": mac}).encode()

    def send(self, packet, uid=None):
        return self.broker.handle(os.getuid() if uid is None else uid, packet)

    def test_good_grant_delivered_once(self):
        packet = self.packet()
        self.assertEqual(self.send(packet), b"OK\n")
        self.assertEqual(self.send(packet), b"DENIED\n")
        self.assertEqual(len(self.delivered), 1)

    def test_same_seq_different_nonce_denied(self):
        self.assertEqual(self.send(self.packet()), b"OK\n")
        self.assertEqual(self.send(self.packet()), b"DENIED\n")

    def test_increasing_sequence_accepted(self):
        self.assertEqual(self.send(self.packet(seq=3)), b"OK\n")
        self.assertEqual(self.send(self.packet(seq=2)), b"DENIED\n")
        self.assertEqual(self.send(self.packet(seq=4)), b"OK\n")

    def test_concurrent_equal_sequence_exactly_one(self):
        packets = [self.packet() for _ in range(12)]
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(self.send, packets))
        self.assertEqual(results.count(b"OK\n"), 1)
        self.assertEqual(results.count(b"DENIED\n"), 11)

    def test_forged_legacy_role_denied(self):
        self.assertEqual(self.send(self.packet(role="viewer")), b"DENIED\n")
        self.assertEqual(self.send(self.packet(role="admin", action="shell")),
                         b"DENIED\n")

    def test_uid_rejected(self):
        self.assertEqual(self.send(self.packet(), os.getuid()+1), b"DENIED\n")

    def test_malformed_nonce_rejected(self):
        self.assertEqual(self.send(self.packet(nonce="*"*32)), b"DENIED\n")

    def test_expired_rejected(self):
        packet = self.packet()
        self.now += 301_000_000
        self.assertEqual(self.send(packet), b"DENIED\n")

    def test_out_of_range_and_boolean_rejected(self):
        for x in (-501, 501, True):
            with self.subTest(x=x):
                self.assertEqual(self.send(self.packet(x=x)), b"DENIED\n")

    def test_signature_rejected(self):
        obj = json.loads(self.packet())
        obj["sig"] = "00"*32
        self.assertEqual(self.send(json.dumps(obj).encode()), b"DENIED\n")

    def test_key_rotation_revokes_session_and_sequence(self):
        old = self.packet()
        self.assertEqual(self.send(old), b"OK\n")
        self.broker.rotate(secrets.token_bytes(32))
        self.assertEqual(self.send(old), b"DENIED\n")
        self.assertEqual(self.broker.last_sequence, 0)

    def test_reject_unsafe_socket_directory(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)
            os.chmod(path, 0o755)
            with self.assertRaises(PermissionError):
                serve(path/"test.sock", self.broker, threading.Event(), threading.Event())

    def test_unix_socket_live_loop_denies_unsigned(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)
            os.chmod(path,0o700)
            sockpath=path/"broker.sock"
            stop=threading.Event()
            ready=threading.Event()
            worker=threading.Thread(target=serve,args=(sockpath,self.broker,stop,ready))
            worker.start()
            try:
                self.assertTrue(ready.wait(2))
                with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as conn:
                    conn.settimeout(2)
                    conn.connect(str(sockpath))
                    conn.sendall(b'{"action":"move","x":1,"y":1}')
                    self.assertEqual(conn.recv(128), b"DENIED\n")
            finally:
                stop.set()
                worker.join(2)
                self.assertFalse(worker.is_alive())


if __name__ == "__main__":
    unittest.main()
