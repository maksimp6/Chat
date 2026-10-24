"""Hardware smoke for an authorized A1 mini on an approved local network.

Disabled unless LOGOS_PRINTER_HARDWARE_IP is explicitly supplied. The
hardware connector is injected because unprivileged Termux cannot use
SO_BINDTODEVICE. No writes beyond the read-only detect command.
"""

from __future__ import annotations

import os
import socket
import statistics

import pytest

from printer_service.printers.lan_address import private_ipv4
from printer_service.printers.persistent_session import PrinterSession


def test_persistent_a1mini_real_network():
    host = os.getenv("LOGOS_PRINTER_HARDWARE_IP")
    if not host:
        pytest.skip("hardware opt-in not configured")
    host = private_ipv4(host)
    expected = os.getenv("LOGOS_PRINTER_EXPECTED_MODEL", "N1")
    measurements = []

    def connector(ip, port, *, timeout):
        assert ip == host and port == 3000
        # This test runs only on an explicitly approved on-LAN test host.
        return socket.create_connection((ip, port), timeout=timeout)

    with PrinterSession(host, connector=connector) as session:
        for _ in range(10):
            identity, rtt_ms = session.detect()
            assert identity["model"] == expected
            assert rtt_ms >= 0
            measurements.append(rtt_ms)
    assert len(measurements) == 10
    print("HARDWARE_PERSISTENT_RTT_MEDIAN_MS", round(statistics.median(measurements), 3))
