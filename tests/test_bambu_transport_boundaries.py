"""Transport boundaries must fail closed."""

import pytest

from printer_service.printers.service import Transport, identify, cloud_reader
from printer_service.printers.cloud_api import CloudApiNotConfigured
from printer_service.printers.lan import require_lan_host


@pytest.mark.parametrize(
    "host",
    [
        "example.com",
        "localhost",
        "printer.local",
        "8.8.8.8",
        "127.0.0.1",
        "169.254.1.1",
        "0.0.0.0",
        "224.0.0.1",
        "100.64.0.1",
        "198.18.0.1",
        "::1",
        "::ffff:192.168.31.12",
        "192.168.31.12.example.com",
        "192.168.31.12:3000",
        "http://192.168.31.12",
        "172.32.0.1",
        "192.0.0.1",
    ],
)
def test_lan_rejects_non_rfc1918_and_dns(host):
    with pytest.raises(ValueError):
        require_lan_host(host)


@pytest.mark.parametrize(
    "host",
    [
        "10.0.0.1",
        "172.16.0.1",
        "172.31.255.254",
        "192.168.31.12",
    ],
)
def test_lan_accepts_rfc1918(host):
    assert require_lan_host(host) == host


def test_lan_dns_resolution_requires_validated_route(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("DNS resolution attempted")

    monkeypatch.setattr(
        "socket.getaddrinfo", lambda *args, **kwargs: [(2, 1, 6, "", ("8.8.8.8", 3000))]
    )
    monkeypatch.setattr("socket.create_connection", fail)
    with pytest.raises(ValueError):
        identify(Transport.LAN, host="printer.local")


def test_lan_rejects_public_before_socket(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("socket opened")

    monkeypatch.setattr("socket.create_connection", fail)
    with pytest.raises(ValueError):
        identify(Transport.LAN, host="8.8.8.8")


def test_no_cloud_discovery():
    with pytest.raises(ValueError):
        identify(Transport.CLOUD_API, host="192.168.31.12")


def test_no_implicit_cloud_fallback():
    with pytest.raises(ValueError):
        cloud_reader(Transport.LAN)


def test_cloud_disabled_by_default():
    with pytest.raises(CloudApiNotConfigured):
        cloud_reader(Transport.CLOUD_API).get_status("printer")


def test_lan_requires_explicit_host():
    with pytest.raises(ValueError):
        identify(Transport.LAN)
