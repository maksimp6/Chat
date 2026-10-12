"""Fail-closed, interface-bound LAN TCP connector.

Linux SO_BINDTODEVICE is required for strong routing isolation. Platforms
without permission or support must fail rather than silently fall back.
"""

from __future__ import annotations

import socket
from typing import Protocol

from .lan_address import resolve_lan


class LanBindingError(OSError):
    """Cannot guarantee the requested LAN interface."""


class SocketFactory(Protocol):
    def __call__(self, family: int, kind: int) -> socket.socket: ...


def connect_lan_impl(
    host: str,
    port: int = 3000,
    *,
    interface: str = "wlan0",
    timeout: float = 3.0,
    socket_factory: SocketFactory | None = None,
) -> socket.socket:
    """Resolve once and bind the socket to the verified interface before connect."""
    if not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("invalid port")
    if not isinstance(interface, str) or not interface or len(interface) > 15:
        raise ValueError("invalid interface")
    if not interface.replace("_", "").replace("-", "").isalnum():
        raise ValueError("invalid interface")
    if not 0 < timeout <= 15:
        raise ValueError("invalid timeout")
    pinned_ip = resolve_lan(host, interface=interface)
    factory = socket_factory if socket_factory is not None else socket.socket
    sock = factory(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.settimeout(timeout)
        option = getattr(socket, "SO_BINDTODEVICE", None)
        if option is None:
            raise LanBindingError("SO_BINDTODEVICE unavailable")
        try:
            sock.setsockopt(socket.SOL_SOCKET, option, interface.encode("ascii") + b"\x00")
        except OSError as exc:
            raise LanBindingError("cannot bind printer socket to LAN interface") from exc
        sock.connect((pinned_ip, port))
        if sock.getpeername()[0] != pinned_ip:
            raise LanBindingError("connected peer differs from pinned IP")
        return sock
    except BaseException:
        sock.close()
        raise


def connect_lan(
    host: str, port: int = 3000, *, interface: str = "wlan0", timeout: float = 3.0
) -> socket.socket:
    return connect_lan_impl(host, port, interface=interface, timeout=timeout)
