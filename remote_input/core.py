"""Alice Mouse input orchestration with signed grants, safe release, and preview.

Trust model:
  * Authentication to controller role occurs in the caller's trusted server.
  * A 32-byte HMAC key must be supplied from a protected channel, not from HTTP.
  * The signed verifier must be placed on the root side before physical dispatch.
  * RootSocketBackend is disabled by default; staging RecordingBackend is safe.
This module is NOT wired to the running Redmi 9 broker and is not a cutover.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import socket
import threading
import time
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

ALLOWED = frozenset(("move", "click", "right", "scroll", "down", "up"))
MAX_PACKET = 1024
MAX_AGE_NS = 300_000_000


class MouseError(RuntimeError):
    pass


@dataclass(frozen=True)
class Command:
    action: str
    x: int = 0
    y: int = 0
    button: int = 1

    def validate(self) -> None:
        if self.action not in ALLOWED or any(
            type(v) is not int for v in (self.x, self.y, self.button)
        ):
            raise MouseError("invalid command")
        if not (-500 <= self.x <= 500 and -500 <= self.y <= 500) or self.button not in (1, 2):
            raise MouseError("out of bounds")
        if self.action in ("down", "up", "right") and (self.x, self.y) != (0, 0):
            raise MouseError("button action requires zero deltas")
        if self.action == "scroll" and (self.y != 0 or abs(self.x) > 20):
            raise MouseError("invalid scroll")
        if self.action == "click" and self.button != 1:
            raise MouseError("invalid click")
        if self.action == "right" and self.button != 2:
            raise MouseError("invalid right click")


@dataclass
class Cursor:
    width: int
    height: int
    x: int = field(init=False)
    y: int = field(init=False)
    rotation: int = 0

    def __post_init__(self) -> None:
        self._check(self.width, self.height, self.rotation)
        self.x, self.y = self.width // 2, self.height // 2

    @staticmethod
    def _check(width: int, height: int, rotation: int) -> None:
        if (
            any(type(n) is not int for n in (width, height, rotation))
            or not (48 <= width <= 8192 and 48 <= height <= 8192)
            or rotation not in (0, 1, 2, 3)
        ):
            raise MouseError("invalid display geometry")

    def update(self, cmd: Command) -> tuple[int, int]:
        if cmd.action in ("move", "click"):
            self.x = max(0, min(self.width - 1, self.x + cmd.x))
            self.y = max(0, min(self.height - 1, self.y + cmd.y))
        return (self.x, self.y)

    def resize(self, width: int, height: int, rotation: int) -> None:
        self._check(width, height, rotation)
        if (width, height, rotation) != (self.width, self.height, self.rotation):
            self.width, self.height, self.rotation = width, height, rotation
            self.x, self.y = width // 2, height // 2


class RecordingBackend:
    """Deterministic test backend; no system calls or real input."""

    def __init__(self) -> None:
        self.events: list[tuple[str, int, int, int]] = []
        self.fail_on: str | None = None

    def emit(self, action: str, x: int = 0, y: int = 0, button: int = 1) -> None:
        if self.fail_on == action:
            raise OSError("injected failure")
        self.events.append((action, x, y, button))


class RootSocketBackend:
    """Opt-in raw root transport. Only safe behind the root-side verifier.

    The existing production broker does not enforce signed grants. Do not use
    this backend in production until that boundary is independently verified.
    """

    def __init__(self, path: str | Path, *, enabled: bool = False) -> None:
        if not enabled:
            raise MouseError("physical input disabled until reviewed deployment")
        self.path = Path(path)

    def emit(self, action: str, x: int = 0, y: int = 0, button: int = 1) -> None:
        # UI action conversion is internal, not a shell command.
        if action in ("down", "up"):
            a, b = button, 0
        elif action == "right":
            a, b = 0, 0
        elif action == "scroll":
            a, b = x, 0
        else:
            a, b = x, y
        data = f"{action} {a} {b}\n".encode("ascii")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
            conn.settimeout(1)
            conn.connect(str(self.path))
            conn.sendall(data)
            if conn.recv(16) != b"OK\n":
                raise MouseError("device rejected command")


def _json(value: dict[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("ascii")


class InputBackend(Protocol):
    """Structural interface; no implicit permission to access root."""

    def emit(self, action: str, x: int = 0, y: int = 0, button: int = 1) -> None: ...


class MouseModule:
    """Single-controller lifecycle with signed command replay protection.

    issue() is trusted-server-only; no public HTTP or command socket exposes it.
    dispatch() requires signed packets and validates session, freshness, seq.
    """

    def __init__(
        self,
        backend: InputBackend,
        *,
        width: int = 2340,
        height: int = 1080,
        clock: Callable[[], int] | None = None,
        preview: Callable[[int, int], None] | None = None,
        hide: Callable[[], None] | None = None,
        focus_ok: Callable[[], bool] | None = None,
    ) -> None:
        self.backend = backend
        self.cursor = Cursor(width, height)
        self.clock = clock or time.monotonic_ns
        self.preview = preview or (lambda x, y: None)
        self.hide = hide or (lambda: None)
        self.focus_ok = focus_ok or (lambda: False)
        self.lock = threading.RLock()
        self.key: bytes | None = None
        self.epoch: str | None = None
        self.session: str | None = None
        self.seq = 0
        self.issued_seq = 0
        self.held: set[int] = set()
        self.deadline_ns: int | None = None
        self.active = False
        self._lease_timer: threading.Timer | None = None
        self._lease_generation = 0

    def _cancel_lease_timer(self) -> None:
        self._lease_generation += 1
        timer = self._lease_timer
        self._lease_timer = None
        if timer is not None:
            timer.cancel()

    def _arm_lease_timer(self) -> None:
        self._cancel_lease_timer()
        generation = self._lease_generation
        timer = threading.Timer(1.5, self._lease_expired, args=(generation,))
        timer.daemon = True
        self._lease_timer = timer
        timer.start()

    def _lease_expired(self, generation: int) -> None:
        with self.lock:
            if generation != self._lease_generation or not self.held:
                return
            try:
                self._release()
            except MouseError:
                pass  # fail closed: active=False; no further input
            finally:
                self.hide()

    def start(self, *, authorized: bool, key: bytes) -> str:
        if authorized is not True or type(key) is not bytes or len(key) != 32:
            raise MouseError("controller authentication and a protected 32-byte key required")
        with self.lock:
            self._release()
            self.key = key
            self.epoch = secrets.token_hex(16)
            self.session = secrets.token_hex(16)
            self.seq = self.issued_seq = 0
            self.active = True
            return self.session

    def issue(self, cmd: Command, *, authorized: bool) -> bytes:
        if authorized is not True:
            raise MouseError("not controller")
        cmd.validate()
        with self.lock:
            if not self.active:
                raise MouseError("no session")
            self.issued_seq += 1
            value = {
                "version": 1,
                "epoch": self.epoch,
                "session": self.session,
                "seq": self.issued_seq,
                "issued_ns": self.clock(),
                "nonce": secrets.token_hex(16),
                "action": cmd.action,
                "x": cmd.x,
                "y": cmd.y,
                "button": cmd.button,
            }
            key = self.key
            if key is None:
                raise MouseError("no signing key")
            mac = hmac.digest(key, _json(value), "sha256").hex()
            packet = _json({"grant": value, "mac": mac})
            if len(packet) > MAX_PACKET:
                raise MouseError("packet too large")
            return packet

    def _fail_foreground(self, *, revoke: bool) -> None:
        """Release physical input before hiding the preview on focus failure."""
        if revoke:
            self.active = False
        try:
            self._release()
        finally:
            self.hide()

    def dispatch(self, packet: bytes) -> tuple[int, int]:
        with self.lock:
            if not self.active or type(packet) is not bytes or not 0 < len(packet) <= MAX_PACKET:
                raise MouseError("unavailable")
            try:
                envelope = json.loads(packet)
                if type(envelope) is not dict or set(envelope) != {"grant", "mac"}:
                    raise ValueError()
                grant = envelope["grant"]
                if type(grant) is not dict or set(grant) != {
                    "version",
                    "epoch",
                    "session",
                    "seq",
                    "issued_ns",
                    "nonce",
                    "action",
                    "x",
                    "y",
                    "button",
                }:
                    raise ValueError()
                signature = bytes.fromhex(envelope["mac"])
                key = self.key
                if (
                    key is None
                    or len(signature) != 32
                    or not hmac.compare_digest(signature, hmac.digest(key, _json(grant), "sha256"))
                ):
                    raise ValueError()
                if (
                    type(grant["version"]) is not int
                    or grant["version"] != 1
                    or grant["epoch"] != self.epoch
                    or grant["session"] != self.session
                    or type(grant["seq"]) is not int
                    or grant["seq"] <= self.seq
                    or type(grant["issued_ns"]) is not int
                    or type(grant["nonce"]) is not str
                    or len(grant["nonce"]) != 32
                    or any(c not in "0123456789abcdef" for c in grant["nonce"])
                ):
                    raise ValueError()
                age = self.clock() - grant["issued_ns"]
                if not 0 <= age <= MAX_AGE_NS:
                    raise ValueError()
                cmd = Command(grant["action"], grant["x"], grant["y"], grant["button"])
                cmd.validate()
            except (
                ValueError,
                TypeError,
                KeyError,
                UnicodeError,
                OverflowError,
                MouseError,
            ) as exc:
                raise MouseError("invalid grant") from exc
            # Advance replay position before any device effect, including failure.
            self.seq = grant["seq"]
            try:
                # Only a definitive trusted foreground signal authorizes input.
                focused = self.focus_ok()
            except Exception as exc:
                # A failing foreground inspector cannot retain a drag lease.
                self._fail_foreground(revoke=True)
                raise MouseError("foreground verification failed; session revoked") from exc
            if focused is not True:
                self._fail_foreground(revoke=False)
                raise MouseError("unsafe foreground")
            self._apply(cmd)
            point = self.cursor.update(cmd)
            self.preview(*point)
            return point

    def _apply(self, cmd: Command) -> None:
        completed = False
        try:
            if cmd.action == "down":
                if cmd.button in self.held:
                    raise MouseError("button already held")
                # Track BEFORE writing; partial writes remain releasable.
                self.held.add(cmd.button)
                self.deadline_ns = self.clock() + 1_500_000_000
                self._arm_lease_timer()
                self.backend.emit("down", button=cmd.button)
            elif cmd.action == "up":
                if cmd.button not in self.held:
                    raise MouseError("button is not held")
                self.backend.emit("up", button=cmd.button)
                self.held.remove(cmd.button)
                if not self.held:
                    self.deadline_ns = None
                    self._cancel_lease_timer()
            elif cmd.action == "click":
                self.backend.emit("move", cmd.x, cmd.y)
                self.held.add(1)
                self._arm_lease_timer()
                self.backend.emit("down", button=1)
                self.backend.emit("up", button=1)
                self.held.remove(1)
                self._cancel_lease_timer()
            elif cmd.action == "right":
                self.held.add(2)
                self._arm_lease_timer()
                self.backend.emit("down", button=2)
                self.backend.emit("up", button=2)
                self.held.remove(2)
                self._cancel_lease_timer()
            elif cmd.action == "scroll":
                self.backend.emit("scroll", cmd.x, 0)
            else:
                self.backend.emit("move", cmd.x, cmd.y)
            completed = True
        finally:
            if not completed:
                try:
                    self._release()
                finally:
                    self.hide()

    def tick(self) -> None:
        with self.lock:
            if self.held and self.deadline_ns is not None and self.clock() >= self.deadline_ns:
                self._release()
                self.hide()

    def _release(self) -> None:
        failed = False
        for button in tuple(self.held):
            # Every held button must be attempted, even after a backend fault.
            # Suppressed errors set failed=True and revoke further input below.
            released = False
            with suppress(Exception):
                self.backend.emit("up", button=button)
                released = True
            if released:
                self.held.discard(button)
            else:
                failed = True
        if not self.held:
            self.deadline_ns = None
            self._cancel_lease_timer()
        if failed:
            self.active = False
            raise MouseError("physical release failed; service locked")

    def close(self) -> None:
        with self.lock:
            self.active = False
            try:
                self._release()
            finally:
                self._cancel_lease_timer()
                self.hide()
                self.key = None
                self.epoch = self.session = None

    def resize(self, width: int, height: int, rotation: int) -> None:
        with self.lock:
            self._release()
            self.cursor.resize(width, height, rotation)
            self.hide()
