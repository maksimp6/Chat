"""Explicit boundary for cloud/API integrations.

Cloud API transport is intentionally NOT implemented. Do not substitute LAN
access codes for cloud credentials. This interface is read-only.
"""

from __future__ import annotations

from typing import Protocol, Mapping, Any


class PrinterCloudReader(Protocol):
    """Cloud-backed adapters must implement this without importing LAN probes."""

    def get_status(self, printer_id: str) -> Mapping[str, Any]:
        """Return authenticated printer status; never issue print commands."""
        ...


class CloudApiNotConfigured(RuntimeError):
    """Cloud API credentials or an approved adapter are not configured."""


class DisabledCloudApi:
    """Fail closed rather than silently trying the LAN connection."""

    def get_status(self, printer_id: str) -> Mapping[str, Any]:
        raise CloudApiNotConfigured("Cloud printer API is not configured")
