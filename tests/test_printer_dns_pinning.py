"""DNS pinning, SSRF and route isolation regression tests."""

import socket
import pytest
from alice_pro.printers.lan_address import resolve_lan, _route_is_local
from alice_pro.printers.bambu_a1mini import probe

ROUTES = (
    "Iface Destination Gateway Flags RefCnt Use Metric Mask MTU Window IRTT\n"
    "wlan0 001FA8C0 00000000 0001 0 0 0 00FFFFFF 0 0 0\n"
)


def fake_dns(addresses):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 3000)) for ip in addresses]


def test_dns_pinned_to_private_ip(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: fake_dns(["192.168.31.12"]))
    assert resolve_lan("printer.example.test", route_table=ROUTES) == "192.168.31.12"


@pytest.mark.parametrize(
    "ips",
    [
        ["8.8.8.8"],
        ["192.168.31.12", "8.8.8.8"],
        ["127.0.0.1"],
        ["169.254.1.1"],
        ["172.16.1.5"],
    ],
)
def test_dns_rejects_public_mixed_and_wrong_subnet(monkeypatch, ips):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: fake_dns(ips))
    with pytest.raises(ValueError):
        resolve_lan("printer.example.test", route_table=ROUTES)


def test_no_second_dns_lookup(monkeypatch):
    calls = []
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *a, **kw: calls.append(a) or fake_dns(["192.168.31.12"])
    )

    class FakeSocket:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def settimeout(self, timeout):
            pass

        def sendall(self, payload):
            pass

        def recv(self, size):
            return b""

    def connect(address, timeout):
        assert address == ("192.168.31.12", 3000)
        return FakeSocket()

    monkeypatch.setattr(socket, "create_connection", connect)
    with pytest.raises(Exception):
        probe("printer.example.test")
    assert len(calls) == 1


def test_route_rejects_tunnel_override():
    vpn = ROUTES + "tun0 0C1FA8C0 00000000 0001 0 0 0 FFFFFFFF 0 0 0\n"
    assert not _route_is_local("192.168.31.12", "wlan0", vpn)


def test_route_rejects_gateway_and_other_interface():
    other = ROUTES.replace("wlan0", "tun0")
    gateway = ROUTES.replace("00000000 0001", "0101A8C0 0003")
    assert not _route_is_local("192.168.31.12", "wlan0", other)
    assert not _route_is_local("192.168.31.12", "wlan0", gateway)


def test_route_fails_closed_if_missing():
    assert not _route_is_local(
        "192.168.31.12", "wlan0", "Iface Destination Gateway Flags RefCnt Use Metric Mask\n"
    )
