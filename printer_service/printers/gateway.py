"""Transport-independent printer contracts and routing policy."""

from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol


class Transport(str, Enum):
    LAN = "lan"
    CLOUD_API = "cloud_api"


class Operation(str, Enum):
    DISCOVER = "discover"
    STATUS = "status"
    CONTROL = "control"


@dataclass(frozen=True)
class PrinterRequest:
    operation: Operation
    payload: dict[str, Any] | None = None


class PrinterBackend(Protocol):
    def execute(self, request: PrinterRequest) -> dict[str, Any]: ...


class TransportUnavailable(RuntimeError):
    pass


class PrinterGateway:
    """Explicit routing. Never silently falls back between LAN and cloud."""

    def __init__(
        self,
        *,
        lan: PrinterBackend | None = None,
        cloud_api: PrinterBackend | None = None,
    ) -> None:
        self._backends = {Transport.LAN: lan, Transport.CLOUD_API: cloud_api}

    def execute(self, transport: Transport, request: PrinterRequest) -> dict[str, Any]:
        if not isinstance(transport, Transport):
            raise TypeError("transport must be an explicit Transport")
        backend = self._backends[transport]
        if backend is None:
            raise TransportUnavailable(f"{transport.value} backend not configured")
        return backend.execute(request)
