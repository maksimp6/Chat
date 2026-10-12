"""LAN transport guard: IP literals only, no DNS, no public or special routes."""
from __future__ import annotations

import ipaddress

from .bambu_a1mini import PrinterProtocolError, decode_probe, probe as _probe

__all__ = ["PrinterProtocolError", "decode_probe", "probe", "require_lan_host"]


def require_lan_host(host: str) -> str:
    """Accept only RFC1918 IPv4 literals; never resolve a hostname.

    This avoids DNS rebinding and public, loopback, link-local, multicast,
    unspecified, and IPv6 addresses. The socket connects to this exact literal.
    """
    try:
        address = ipaddress.IPv4Address(host)
    except (ipaddress.AddressValueError, TypeError) as exc:
        raise ValueError("LAN host must be an RFC1918 IPv4 literal") from exc
    networks = (
        ipaddress.IPv4Network("10.0.0.0/8"),
        ipaddress.IPv4Network("172.16.0.0/12"),
        ipaddress.IPv4Network("192.168.0.0/16"),
    )
    if not any(address in network for network in networks):
        raise ValueError("LAN host must be RFC1918 IPv4")
    return str(address)


def probe(host: str, timeout: float = 3.0):
    return _probe(require_lan_host(host), timeout=timeout)
