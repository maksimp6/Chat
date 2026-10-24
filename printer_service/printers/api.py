"""Application-facing printer API, isolated from LAN discovery.

The application consumes this adapter rather than reaching into LAN sockets.
Cloud implementations must be injected explicitly; there is no fallback.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from printer_service.printers.models import PrinterIdentity, summarize_status


class PrinterReader(Protocol):
    """Read-only interface implemented by an authorized transport."""

    def identity(self) -> PrinterIdentity: ...
    def status(self) -> dict[str, Any]: ...


@dataclass(frozen=True)
class PrinterService:
    reader: PrinterReader

    def describe(self) -> dict[str, Any]:
        identity = self.reader.identity()
        return {
            "model": identity.model,
            "firmware": identity.firmware,
            "connection": identity.connection,
            "bound": identity.bound,
        }

    def status(self) -> dict[str, Any]:
        return summarize_status(self.reader.status())


class LanReader:
    """LAN identity only. MQTT telemetry requires a separate authorized reader."""

    def __init__(self, host: str, timeout: float = 3.0) -> None:
        self.host = host
        self.timeout = timeout

    def identity(self) -> PrinterIdentity:
        from printer_service.printers.lan import probe

        return probe(self.host, timeout=self.timeout)

    def status(self) -> dict[str, Any]:
        raise NotImplementedError("LAN telemetry requires authorized MQTT reader")


class CloudReader:
    """Explicit cloud boundary; deliberately unavailable without integration."""

    def identity(self) -> PrinterIdentity:
        raise NotImplementedError("Cloud identity API is not configured")

    def status(self) -> dict[str, Any]:
        raise NotImplementedError("Cloud status API is not configured")
