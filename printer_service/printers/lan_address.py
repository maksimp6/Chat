"""Resolve and pin LAN destinations before opening any printer socket."""

from __future__ import annotations
import ipaddress
import socket
from pathlib import Path

_RFC1918 = tuple(
    map(ipaddress.IPv4Network, ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
)


def private_ipv4(value: str) -> str:
    try:
        ip = ipaddress.IPv4Address(value)
    except (ValueError, TypeError) as exc:
        raise ValueError("invalid IPv4 literal") from exc
    if not any(ip in net for net in _RFC1918):
        raise ValueError("not an RFC1918 address")
    return str(ip)


def _route_is_local(ip: str, interface: str, routes: str) -> bool:
    """Reject any more-specific route to a different interface or via gateway."""
    address = int(ipaddress.IPv4Address(ip))
    matches: list[tuple[int, int, str, int]] = []
    for line in routes.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 8:
            continue
        iface, dest, gateway, flags, _, _, metric, mask = parts[:8]
        try:
            network = int.from_bytes(bytes.fromhex(dest), "little")
            netmask = int.from_bytes(bytes.fromhex(mask), "little")
            gw = int.from_bytes(bytes.fromhex(gateway), "little")
            flag = int(flags, 16)
            if flag & 1 and (address & netmask) == (network & netmask):
                matches.append((netmask.bit_count(), -int(metric), iface, gw))
        except ValueError:
            continue
    if not matches:
        return False
    _, _, selected_iface, selected_gateway = max(matches)
    return selected_iface == interface and selected_gateway == 0


def resolve_lan(
    host: str, *, interface: str = "wlan0", route_table: str | None = None
) -> str:
    """Resolve once, validate every result, then return the pinned IP literal.

    Caller must connect only to the returned IP, never resolve the hostname again.
    """
    if (
        not isinstance(host, str)
        or not host
        or any(x in host for x in ("/", "@", ":", "\\", " "))
    ):
        raise ValueError("invalid LAN hostname")
    try:
        addresses = [private_ipv4(host)]
    except ValueError:
        if (
            len(host) > 253
            or host.endswith(".")
            or not all(
                label and len(label) <= 63 and label.replace("-", "").isalnum()
                for label in host.split(".")
            )
        ):
            raise ValueError("invalid DNS hostname") from None
        records = socket.getaddrinfo(
            host, 3000, family=socket.AF_INET, type=socket.SOCK_STREAM
        )
        addresses = [private_ipv4(str(item[4][0])) for item in records]
        if not addresses:
            raise ValueError("empty DNS response") from None
    if route_table is None:
        route_table = Path("/proc/net/route").read_text(encoding="ascii")
    for ip in addresses:
        if not _route_is_local(ip, interface, route_table):
            raise ValueError(
                "LAN destination route is not directly connected via approved interface"
            )
    return addresses[0]
