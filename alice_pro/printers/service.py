"""Explicit transport selection; no fallback across network trust boundaries."""
from enum import Enum
from .models import PrinterIdentity
from .lan import probe
from .cloud_api import DisabledCloudApi


class Transport(str, Enum):
    LAN = "lan"
    CLOUD_API = "cloud_api"


def identify(mode: Transport, *, host: str | None = None) -> PrinterIdentity:
    if mode is not Transport.LAN:
        raise ValueError("device discovery is LAN-only")
    if not host:
        raise ValueError("LAN host required")
    return probe(host)


def cloud_reader(mode: Transport) -> DisabledCloudApi:
    if mode is not Transport.CLOUD_API:
        raise ValueError("cloud reader requires explicit cloud_api mode")
    return DisabledCloudApi()
