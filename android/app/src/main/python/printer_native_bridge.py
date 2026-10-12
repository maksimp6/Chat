"""In-process Chaquopy to Kotlin bridge, not an HTTP or WebView endpoint.

Only the Android application bootstrap can install a Context. The native side
uses Network.bindSocket on a selected Wi-Fi Network; no root or process binding.
"""
from __future__ import annotations

from typing import Any

_context: Any = None


def install_context(context: Any) -> None:
    """Called by trusted Android MainActivity before Python server startup."""
    global _context
    if context is None:
        raise ValueError("Android context is required")
    _context = context.getApplicationContext()


def discover(hostname: str) -> dict[str, str]:
    """Read-only printer identity; no remote HTTP route is registered."""
    if _context is None:
        raise RuntimeError("native Android printer bridge not initialized")
    if not isinstance(hostname, str) or not hostname:
        raise ValueError("invalid hostname")
    from java import jclass  # type: ignore[import-not-found]

    native = jclass("com.alicepro.mobile.printer.BambuWifiDiscovery")
    result = native.discover(_context, hostname)
    return {
        "pinned_ip": str(result.getPinnedIp()),
        "model_code": str(result.getModelCode()),
        "network_transport": "WIFI",
    }
