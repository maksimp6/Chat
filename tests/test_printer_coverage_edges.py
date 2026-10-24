"""Cover every changed failure branch without contacting a real printer."""

import socket

import pytest

from printer_service.printers.api import CloudReader, LanReader
from printer_service.printers.bambu_a1mini import PrinterProtocolError, decode_probe
from printer_service.printers.lan_address import _route_is_local, resolve_lan
from printer_service.printers.lan_socket import connect_lan_impl


def test_reader_identity_delegates(monkeypatch):
    seen = []
    monkeypatch.setattr(
        "printer_service.printers.lan.probe",
        lambda host, timeout: seen.append((host, timeout)) or "identity",
    )
    assert LanReader("printer.local", 2.0).identity() == "identity"
    assert seen == [("printer.local", 2.0)]


def test_cloud_identity_fails_closed():
    with pytest.raises(NotImplementedError):
        CloudReader().identity()


def test_non_object_json_frame_rejected():
    payload = b"[]"
    frame = b"\xa5\xa5" + (len(payload) + 6).to_bytes(2, "little") + payload + b"\xa7\xa7"
    with pytest.raises(PrinterProtocolError, match="expected JSON object"):
        decode_probe(frame)


def test_malformed_route_rows_are_ignored():
    header = "Iface Destination Gateway Flags RefCnt Use Metric Mask MTU Window IRTT\n"
    assert not _route_is_local("192.168.31.12", "wlan0", header + "bad\n")
    assert not _route_is_local(
        "192.168.31.12", "wlan0", header + "wlan0 ZZZ 00000000 0001 0 0 0 00FFFFFF 0 0 0\n"
    )


@pytest.mark.parametrize("host", ["", "bad/name", "bad:3000", "printer.local.", "bad..name"])
def test_invalid_hostname_fails_before_dns(monkeypatch, host):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kw: pytest.fail("DNS called"))
    with pytest.raises(ValueError):
        resolve_lan(host, route_table="")


def test_empty_dns_answer_fails(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kw: [])
    with pytest.raises(ValueError, match="empty DNS"):
        resolve_lan("printer.example.test", route_table="")


@pytest.mark.parametrize(
    "options",
    [
        {"port": 0},
        {"port": 65536},
        {"interface": ""},
        {"interface": "a" * 16},
        {"interface": "wlan/0"},
        {"timeout": 0},
    ],
)
def test_invalid_socket_options_fail_before_dns(monkeypatch, options):
    monkeypatch.setattr(
        "printer_service.printers.lan_socket.resolve_lan", lambda *a, **kw: pytest.fail("resolver called")
    )
    with pytest.raises(ValueError):
        connect_lan_impl("printer.example.test", **options)
