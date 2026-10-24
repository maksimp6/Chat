"""Mock Bambu A1 mini TCP discovery: no physical printer, network, or secrets."""

import json
import socket
import struct

import pytest

from printer_service.printers.bambu_a1mini import PrinterProtocolError, probe


def framed(payload):
    body = json.dumps(payload, separators=(",", ":")).encode()
    return b"\xa5\xa5" + struct.pack("<H", len(body) + 6) + body + b"\xa7\xa7"


class MockPrinterSocket:
    def __init__(self, reply, *, fragment=3):
        self.reply = reply
        self.fragment = fragment
        self.sent = b""
        self.timeout = None
        self.peer = ("192.168.31.12", 3000)

    def getpeername(self):
        return self.peer

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def settimeout(self, value):
        self.timeout = value

    def sendall(self, payload):
        self.sent += payload

    def recv(self, count):
        chunk = self.reply[: min(count, self.fragment)]
        self.reply = self.reply[len(chunk) :]
        return chunk


@pytest.fixture
def mock_printer(monkeypatch):
    sockets = []
    connections = []

    def connect(host, port, timeout):
        connections.append(((host, port), timeout))
        sock = MockPrinterSocket(
            framed(
                {
                    "login": {
                        "command": "detect",
                        "model": "N1",
                        "version": "01.08.01.00",
                        "connect": "cloud",
                        "bind": "occupied",
                    }
                }
            )
        )
        sockets.append(sock)
        sock.settimeout(timeout)
        return sock

    monkeypatch.setattr("printer_service.printers.lan_socket.connect_lan", connect)
    return sockets, connections


def test_mock_discovery_fragmented_tcp(mock_printer):
    sockets, connections = mock_printer
    identity = probe("printer.example.test")
    assert identity.model_code == "N1"
    assert identity.model == "Bambu Lab A1 mini"
    assert identity.firmware == "01.08.01.00"
    assert identity.host == "192.168.31.12"
    assert connections == [(("printer.example.test", 3000), 3.0)]
    assert sockets[0].timeout == 3.0
    sent = sockets[0].sent
    assert sent.startswith(b"\xa5\xa5")
    assert json.loads(sent[4:-2]) == {"login": {"command": "detect", "sequence_id": "1"}}
    assert b"print" not in sent and b"heat" not in sent


@pytest.mark.parametrize(
    "reply",
    [
        b"",
        b"\x00\x00\x06\x00\xa7\xa7",
        framed({"login": {"command": "detect"}}),
        framed({"login": {"command": "unexpected", "model": "N1"}}),
    ],
)
def test_mock_malformed_and_missing_identity(monkeypatch, reply):
    monkeypatch.setattr(
        "printer_service.printers.lan_socket.connect_lan", lambda *a, **kw: MockPrinterSocket(reply)
    )
    with pytest.raises(PrinterProtocolError):
        probe("printer.example.test")


def test_mock_rejects_invalid_timeout_without_network(monkeypatch):
    monkeypatch.setattr(
        "printer_service.printers.lan_socket.connect_lan", lambda *a, **kw: pytest.fail("network used")
    )
    with pytest.raises(ValueError):
        probe("printer.example.test", timeout=0)
