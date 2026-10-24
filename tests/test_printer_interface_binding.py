"""Race and fault injection for interface-bound LAN sockets."""

import socket

import pytest

from alice_pro.printers.lan_socket import LanBindingError, connect_lan_impl as connect_lan


class FakeSocket:
    def __init__(self, *, bind_error=None, connect_error=None, peer="192.168.31.12"):
        self.bind_error = bind_error
        self.connect_error = connect_error
        self.peer = peer
        self.events = []
        self.closed = False

    def settimeout(self, timeout):
        self.events.append(("timeout", timeout))

    def setsockopt(self, level, option, value):
        self.events.append(("bind", value))
        if self.bind_error:
            raise self.bind_error

    def connect(self, address):
        self.events.append(("connect", address))
        if self.connect_error:
            raise self.connect_error

    def getpeername(self):
        return (self.peer, 3000)

    def close(self):
        self.closed = True


@pytest.fixture
def pinned(monkeypatch):
    monkeypatch.setattr(
        "alice_pro.printers.lan_socket.resolve_lan", lambda *a, **kw: "192.168.31.12"
    )
    monkeypatch.setattr(
        socket, "SO_BINDTODEVICE", getattr(socket, "SO_BINDTODEVICE", 25), raising=False
    )


def test_bind_before_connect(pinned):
    fake = FakeSocket()
    result = connect_lan("printer.example.test", socket_factory=lambda *a: fake)
    assert result is fake
    assert fake.events[1][0] == "bind"
    assert fake.events[2] == ("connect", ("192.168.31.12", 3000))
    assert not fake.closed


def test_bind_permission_denied_never_connects(pinned):
    fake = FakeSocket(bind_error=PermissionError("denied"))
    with pytest.raises(LanBindingError):
        connect_lan("printer.example.test", socket_factory=lambda *a: fake)
    assert fake.closed
    assert not any(event[0] == "connect" for event in fake.events)


def test_route_change_after_resolution_cannot_redirect_peer(pinned):
    fake = FakeSocket(peer="10.1.2.3")
    with pytest.raises(LanBindingError):
        connect_lan("printer.example.test", socket_factory=lambda *a: fake)
    assert fake.closed


def test_connect_timeout_closes_socket(pinned):
    fake = FakeSocket(connect_error=socket.timeout("network timeout"))
    with pytest.raises(socket.timeout):
        connect_lan("printer.example.test", socket_factory=lambda *a: fake)
    assert fake.closed


def test_unavailable_bind_option_fails_closed(monkeypatch, pinned):
    fake = FakeSocket()
    monkeypatch.delattr(socket, "SO_BINDTODEVICE", raising=False)
    with pytest.raises(LanBindingError):
        connect_lan("printer.example.test", socket_factory=lambda *a: fake)
    assert fake.closed
    assert not any(event[0] == "connect" for event in fake.events)
