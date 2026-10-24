"""Mock contract tests; never connect to a real Android device."""

import pytest

from printer_service.printers.android_bridge_contract import (
    AndroidBridgeUnavailable,
    DiscoveryRequest,
    DiscoveryResult,
    discover_via_android,
)


class FakeBridge:
    def __init__(self, result):
        self.result = result
        self.requests = []

    def discover(self, request):
        self.requests.append(request)
        return self.result


def test_authorized_wifi_discovery():
    bridge = FakeBridge(DiscoveryResult("192.168.31.12", "N1", "WIFI"))
    assert discover_via_android(bridge, "printer.local").model_code == "N1"
    assert bridge.requests == [DiscoveryRequest("printer.local")]


def test_bridge_missing_fails_closed():
    with pytest.raises(AndroidBridgeUnavailable):
        discover_via_android(None, "printer.local")


@pytest.mark.parametrize(
    "result",
    [
        DiscoveryResult("192.168.31.12", "N1", "CELLULAR"),
        DiscoveryResult("8.8.8.8", "N1", "WIFI"),
        DiscoveryResult("192.168.31.12", "", "WIFI"),
    ],
)
def test_untrusted_result_rejected(result):
    with pytest.raises((AndroidBridgeUnavailable, ValueError)):
        discover_via_android(FakeBridge(result), "printer.local")


@pytest.mark.parametrize("host", ["", "https://printer.local", "printer.local:3000", "a/b"])
def test_invalid_hostname_rejected(host):
    with pytest.raises(ValueError):
        DiscoveryRequest(host)


def test_write_ports_rejected():
    with pytest.raises(ValueError):
        DiscoveryRequest("printer.local", port=8883)
