"""Contract for an Android-owned, interface-bound printer connector.

This module intentionally cannot create a normal unbound Python socket. The
Android process must bind its socket using ConnectivityManager.Network APIs,
then return only verified read-only discovery results over an authenticated
local IPC channel. No process-wide network binding or privilege escalation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class AndroidBridgeUnavailable(RuntimeError):
    """A trusted Android network bridge is not installed or authenticated."""


@dataclass(frozen=True)
class DiscoveryRequest:
    hostname: str
    port: int = 3000

    def __post_init__(self) -> None:
        if not self.hostname or any(ch in self.hostname for ch in "/\\:@ "):
            raise ValueError("invalid hostname")
        if self.port != 3000:
            raise ValueError("only read-only discovery port is permitted")


@dataclass(frozen=True)
class DiscoveryResult:
    pinned_ip: str
    model_code: str
    network_transport: str


class AuthorizedAndroidBridge(Protocol):
    def discover(self, request: DiscoveryRequest) -> DiscoveryResult: ...


def discover_via_android(bridge: AuthorizedAndroidBridge | None, hostname: str) -> DiscoveryResult:
    """Fail closed if the authorized native bridge has not been provided."""
    if bridge is None:
        raise AndroidBridgeUnavailable("Android Network.bindSocket bridge unavailable")
    result = bridge.discover(DiscoveryRequest(hostname))
    if result.network_transport != "WIFI":
        raise AndroidBridgeUnavailable("printer bridge did not use Wi-Fi")
    from printer_service.printers.lan_address import private_ipv4

    private_ipv4(result.pinned_ip)
    if not result.model_code:
        raise AndroidBridgeUnavailable("missing printer identity")
    return result
