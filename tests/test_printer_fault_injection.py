"""Fault injection against the real LAN resolver and printer frame reader.

Only the OS DNS, route-table read and TCP socket are mocked. The resolver,
validation, pinning and protocol parser execute without substitution.
"""
import json
import socket
import struct
from pathlib import Path

import pytest

from alice_pro.printers.bambu_a1mini import PrinterProtocolError, probe

ROUTES = (
    "Iface Destination Gateway Flags RefCnt Use Metric Mask MTU Window IRTT\n"
    "wlan0 001FA8C0 00000000 0001 0 0 0 00FFFFFF 0 0 0\n"
)


class Socket:
    def __init__(self, response):
        self.response = response
        self.sent = b""

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def settimeout(self, _timeout):
        return None

    def sendall(self, payload):
        self.sent = payload

    def recv(self, size):
        if isinstance(self.response, Exception):
            raise self.response
        result, self.response = self.response[:size], self.response[size:]
        return result


def frame(message):
    payload = json.dumps(message).encode()
    return b"\xa5\xa5" + struct.pack("<H", len(payload) + 6) + payload + b"\xa7\xa7"


@pytest.fixture
def network(monkeypatch):
    dns_calls = []
    connections = []
    monkeypatch.setattr(Path, "read_text", lambda self, **kw: ROUTES)

    def resolve(host, port, **_kw):
        dns_calls.append(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.31.12", port))]

    monkeypatch.setattr(socket, "getaddrinfo", resolve)

    def connect(address, timeout):
        connections.append(address)
        return Socket(frame({"login": {"command": "detect", "model": "N1"}}))

    monkeypatch.setattr(socket, "create_connection", connect)
    return dns_calls, connections


def test_real_resolver_dns_is_pinned(network):
    dns_calls, connections = network
    assert probe("printer.example.test").model_code == "N1"
    assert dns_calls == ["printer.example.test"]
    assert connections == [("192.168.31.12", 3000)]


def test_dns_public_answer_never_opens_socket(monkeypatch, network):
    dns_calls, connections = network
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", port))],
    )
    with pytest.raises(ValueError):
        probe("printer.example.test")
    assert connections == []
    assert dns_calls == []


def test_dns_route_vpn_override_never_opens_socket(monkeypatch, network):
    _dns_calls, connections = network
    vpn = ROUTES + "tun0 0C1FA8C0 00000000 0001 0 0 0 FFFFFFFF 0 0 0\n"
    monkeypatch.setattr(Path, "read_text", lambda self, **kw: vpn)
    with pytest.raises(ValueError):
        probe("printer.example.test")
    assert connections == []


@pytest.mark.parametrize(
    "response",
    [
    b"",
    b"\xa5\xa5\x00\x00",
    b"\xa5\xa5\xff\xff",
    frame({"login": {"command": "detect", "model": "N1"}})[:-2],
])
def test_corrupt_or_truncated_frames(monkeypatch, network, response):
    monkeypatch.setattr(socket, "create_connection", lambda *args, **kw: Socket(response))
    with pytest.raises(PrinterProtocolError):
        probe("printer.example.test")


def test_socket_timeout_propagates(monkeypatch, network):
    monkeypatch.setattr(
        socket,
        "create_connection",
        lambda *args, **kw: Socket(socket.timeout("printer timeout")),
    )
    with pytest.raises(socket.timeout):
        probe("printer.example.test")
