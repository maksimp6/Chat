"""Fail-closed Android backend facade. Simulated evidence is never real evidence."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol
import threading
import struct
import zlib
import time


class Backend(str, Enum):
    SIMULATED = "simulated"
    EMULATOR = "emulator"
    DEVICE = "device"


class Keyguard(str, Enum):
    LOCKED = "locked"
    UNLOCKED = "unlocked"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Evidence:
    backend: Backend
    keyguard: Keyguard
    verified: bool
    provenance: str


@dataclass
class SimulatedAndroid:
    keyguard: Keyguard = Keyguard.LOCKED
    width: int = 360
    height: int = 800
    events: list[dict[str, object]] = field(default_factory=list)
    faults: set[str] = field(default_factory=set)

    def observe(self) -> Evidence:
        if "observation_unavailable" in self.faults:
            return Evidence(Backend.SIMULATED, Keyguard.UNKNOWN, False, "simulation")
        return Evidence(Backend.SIMULATED, self.keyguard, False, "simulation")

    def tap(self, x: int, y: int) -> bool:
        if self.keyguard != Keyguard.UNLOCKED or "transport_failure" in self.faults:
            return False
        if not (0 <= x < self.width and 0 <= y < self.height):
            return False
        self.events.append({"action": "tap", "x": x, "y": y})
        return True

    def swipe(self, x1: int, y1: int, x2: int, y2: int) -> bool:
        if self.keyguard != Keyguard.UNLOCKED or "transport_failure" in self.faults:
            return False
        if not all((0 <= x < self.width and 0 <= y < self.height) for x, y in ((x1, y1), (x2, y2))):
            return False
        self.events.append({"action": "swipe", "start": [x1, y1], "end": [x2, y2]})
        return True

    def screenshot(self) -> bytes:
        if "capture_failure" in self.faults:
            raise RuntimeError("simulated capture failure")
        # Valid deterministic 1x1 PNG fixture, not a real screen capture.
        import base64
        return base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6S0sAAAAASUVORK5CYII=")


class AndroidTransport(Protocol):
    def observe(self) -> Evidence: ...
    def tap(self, x: int, y: int) -> bool: ...
    def swipe(self, x1: int, y1: int, x2: int, y2: int) -> bool: ...
    def screenshot(self) -> bytes: ...


@dataclass
class UnconfiguredTransport:
    backend: Backend

    def observe(self) -> Evidence:
        return Evidence(self.backend, Keyguard.UNKNOWN, False, "unconfigured")

    def tap(self, x: int, y: int) -> bool:
        raise RuntimeError("transport not configured")

    def swipe(self, x1: int, y1: int, x2: int, y2: int) -> bool:
        raise RuntimeError("transport not configured")

    def screenshot(self) -> bytes:
        raise RuntimeError("transport not configured")


def validate_png(data: bytes) -> bool:
    """Validate bounded PNG structure, chunk CRCs and terminal IEND."""
    if len(data) < 45 or len(data) > 16 * 1024 * 1024:
        return False
    if not data.startswith(bytes.fromhex("89504e470d0a1a0a")):
        return False
    offset = 8
    seen_ihdr = False
    seen_idat = False
    while offset + 12 <= len(data):
        length = struct.unpack_from(">I", data, offset)[0]
        end = offset + 12 + length
        if length > 16 * 1024 * 1024 or end > len(data):
            return False
        kind = data[offset + 4 : offset + 8]
        payload = data[offset + 8 : offset + 8 + length]
        expected = struct.unpack_from(">I", data, offset + 8 + length)[0]
        if zlib.crc32(kind + payload) != expected:
            return False
        if not seen_ihdr:
            if kind != b"IHDR" or length != 13:
                return False
            width, height = struct.unpack_from(">II", payload)
            if not (0 < width <= 8192 and 0 < height <= 8192):
                return False
            seen_ihdr = True
        elif kind == b"IDAT":
            seen_idat = True
        elif kind == b"IEND":
            return seen_idat and length == 0 and end == len(data)
        offset = end
    return False


class AndroidShim:
    def __init__(self, backend: Backend = Backend.SIMULATED, transport: AndroidTransport | None = None):
        self.backend = Backend(backend)
        self.transport = transport if transport is not None else (
            SimulatedAndroid() if self.backend == Backend.SIMULATED else UnconfiguredTransport(self.backend)
        )
        self.trace: list[dict[str, object]] = []
        self._lock = threading.RLock()

    def _record(self, action: str, outcome: str) -> None:
        self.trace.append({"backend": self.backend.value, "action": action, "outcome": outcome, "timestamp_ns": time.monotonic_ns()})

    def observe(self) -> Evidence:
        result = self.transport.observe()
        if result.backend != self.backend:
            self._record("observe", "backend_mismatch")
            raise RuntimeError("backend mismatch")
        # No configured trust anchor exists yet for real backends.
        result = Evidence(self.backend, result.keyguard, False, "simulation" if self.backend == Backend.SIMULATED else "unverified_transport")
        self._record("observe", result.keyguard.value)
        return result

    def tap(self, x: int, y: int) -> bool:
        with self._lock:
            if self.backend != Backend.SIMULATED or self.observe().keyguard != Keyguard.UNLOCKED:
                self._record("tap", "denied_keyguard")
                return False
            ok = self.transport.tap(x, y)
            self._record("tap", "submitted" if ok else "rejected")
            return ok

    def swipe(self, x1: int, y1: int, x2: int, y2: int) -> bool:
        with self._lock:
            if self.observe().keyguard != Keyguard.UNLOCKED:
                self._record("swipe", "denied_keyguard")
                return False
            ok = self.transport.swipe(x1, y1, x2, y2)
            self._record("swipe", "submitted" if ok else "rejected")
            return ok

    def screenshot(self) -> bytes:
        data = self.transport.screenshot()
        if not validate_png(data):
            self._record("screenshot", "invalid")
            raise ValueError("invalid screenshot")
        self._record(
            "screenshot",
            "simulated" if self.backend == Backend.SIMULATED else "captured_unverified",
        )
        return data
