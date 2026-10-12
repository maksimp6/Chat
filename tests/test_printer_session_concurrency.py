"""Regression tests for persistent TCP concurrency, failures and socket options."""

from __future__ import annotations

import json
import socket
import struct
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from alice_pro.printers.bambu_a1mini import PrinterProtocolError
from alice_pro.printers.persistent_session import PrinterSession


class Socket:
    def __init__(self, *, bad_header=False, bad_length=False, bad_login=False, bad_seq=False):
        self.closed = False
        self.options = []
        self.buffer = b""
        self.active = 0
        self.max_active = 0
        self.guard = threading.Lock()
        self.bad_header = bad_header
        self.bad_length = bad_length
        self.bad_login = bad_login
        self.bad_seq = bad_seq

    def setsockopt(self, *args):
        self.options.append(args)

    def sendall(self, data):
        with self.guard:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        time.sleep(0.001)
        sequence = json.loads(data[4:-2])["login"]["sequence_id"]
        body = json.dumps(
            {
                "login": {
                    "command": "other" if self.bad_login else "detect",
                    "model": "N1",
                    "sequence_id": "wrong" if self.bad_seq else sequence,
                }
            }
        ).encode()
        size = len(body) + 6
        self.buffer = (
            (
                b"BAD!"
                if self.bad_header
                else b"\xa5\xa5" + struct.pack("<H", 1 if self.bad_length else size)
            )
            + body
            + b"\xa7\xa7"
        )

    def recv(self, count):
        data, self.buffer = self.buffer[:count], self.buffer[count:]
        if not self.buffer:
            with self.guard:
                self.active -= 1
        return data

    def close(self):
        self.closed = True


def test_concurrent_detect_is_serialized():
    sock = Socket()
    with PrinterSession("192.168.31.12", connector=lambda *a, **k: sock) as session:
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: session.detect(), range(24)))
    assert len(results) == 24
    assert all(item[0]["model"] == "N1" for item in results)
    assert sock.max_active == 1


def test_second_enter_rejected():
    sock = Socket()
    with PrinterSession("192.168.31.12", connector=lambda *a, **k: sock) as session:
        with pytest.raises(RuntimeError, match="already open"):
            session.__enter__()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"bad_header": True},
        {"bad_length": True},
        {"bad_login": True},
        {"bad_seq": True},
    ],
)
def test_invalid_frame_closes_connection(kwargs):
    sock = Socket(**kwargs)
    with PrinterSession("192.168.31.12", connector=lambda *a, **k: sock) as session:
        with pytest.raises(PrinterProtocolError):
            session.detect()
    assert sock.closed


def test_tcp_flags_are_explicit_and_set_before_request():
    sock = Socket()
    with PrinterSession(
        "192.168.31.12",
        connector=lambda *a, **k: sock,
        tcp_nodelay=True,
        keepalive=True,
    ) as session:
        assert (socket.IPPROTO_TCP, socket.TCP_NODELAY, 1) in sock.options
        assert (socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1) in sock.options
        session.detect()


def test_option_failure_closes_socket():
    class Failing(Socket):
        def setsockopt(self, *args):
            raise OSError("unsupported")

    sock = Failing()
    with pytest.raises(OSError):
        with PrinterSession("192.168.31.12", connector=lambda *a, **k: sock, keepalive=True):
            pass
    assert sock.closed
