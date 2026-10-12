"""Persistent connection contract and failure isolation."""
import json
import struct

import pytest

from alice_pro.printers.bambu_a1mini import PrinterProtocolError
from alice_pro.printers.persistent_session import PrinterSession


class FakeSocket:
    def __init__(self, *, corrupt=False):
        self.closed = False
        self.requests = []
        self.buffer = b""
        self.corrupt = corrupt

    def sendall(self, frame):
        self.requests.append(frame)
        payload = json.loads(frame[4:-2])
        seq = payload["login"]["sequence_id"]
        response = {"login": {"command": "detect", "model": "N1", "sequence_id": seq}}
        body = json.dumps(response).encode()
        self.buffer = b"\xa5\xa5" + struct.pack("<H", len(body) + 6) + body + b"\xa7\xa7"
        if self.corrupt:
            self.buffer = b"BAD!" + self.buffer[4:]

    def recv(self, count):
        part, self.buffer = self.buffer[:count], self.buffer[count:]
        return part

    def close(self):
        self.closed = True


def test_one_connection_multiple_requests():
    sock = FakeSocket()
    calls = []
    def connect(*args, **kwargs):
        calls.append((args, kwargs))
        return sock
    with PrinterSession("192.168.31.12", connector=connect) as session:
        first, first_ms = session.detect()
        second, second_ms = session.detect()
        assert first["model"] == second["model"] == "N1"
        assert first_ms >= 0 and second_ms >= 0
        assert len(sock.requests) == 2
        assert len(calls) == 1
        assert json.loads(sock.requests[0][4:-2])["login"]["sequence_id"] == "1"
        assert json.loads(sock.requests[1][4:-2])["login"]["sequence_id"] == "2"
    assert sock.closed


def test_protocol_error_closes_and_never_retries():
    sock = FakeSocket(corrupt=True)
    with PrinterSession("192.168.31.12", connector=lambda *a, **kw: sock) as session:
        with pytest.raises(PrinterProtocolError):
            session.detect()
        assert sock.closed
        with pytest.raises(RuntimeError):
            session.detect()


def test_unconnected_session_rejected():
    with pytest.raises(RuntimeError):
        PrinterSession("192.168.31.12").detect()
