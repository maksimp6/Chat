"""Fail-closed Android backend facade. Simulated evidence is never real evidence."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol
import hashlib
import json
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
    events: list[dict] = field(default_factory=list)
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


class AndroidShim:
    def __init__(self, backend: Backend = Backend.SIMULATED, transport: AndroidTransport | None = None):
        self.backend = Backend(backend)
        self.transport = transport if transport is not None else (
            SimulatedAndroid() if self.backend == Backend.SIMULATED else UnconfiguredTransport(self.backend)
        )
        self.trace: list[dict] = []

    def _record(self, action: str, outcome: str) -> None:
        self.trace.append({"backend": self.backend.value, "action": action, "outcome": outcome, "timestamp_ns": time.monotonic_ns()})

    def observe(self) -> Evidence:
        result = self.transport.observe()
        if result.backend != self.backend:
            self._record("observe", "backend_mismatch")
            raise RuntimeError("backend mismatch")
        if self.backend == Backend.SIMULATED:
            result = Evidence(Backend.SIMULATED, result.keyguard, False, "simulation")
        self._record("observe", result.keyguard.value)
        return result

    def tap(self, x: int, y: int) -> bool:
        if self.observe().keyguard != Keyguard.UNLOCKED:
            self._record("tap", "denied_keyguard")
            return False
        ok = self.transport.tap(x, y)
        self._record("tap", "submitted" if ok else "rejected")
        return ok

    def swipe(self, x1: int, y1: int, x2: int, y2: int) -> bool:
        if self.observe().keyguard != Keyguard.UNLOCKED:
            self._record("swipe", "denied_keyguard")
            return False
        ok = self.transport.swipe(x1, y1, x2, y2)
        self._record("swipe", "submitted" if ok else "rejected")
        return ok

    def screenshot(self) -> bytes:
        data = self.transport.screenshot()
        if not data.startswith(b"\\x89PNG\\r\\n\\x1a\\n"):
            self._record("screenshot", "invalid")
            raise ValueError("invalid screenshot")
        self._record("screenshot", "simulated" if self.backend == Backend.SIMULATED else "captured_unverified")
        return data
