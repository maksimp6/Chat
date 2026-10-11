"""Fast (<2s) fail-closed Alice Input emulation; never touches /dev/uinput.

This is an early developer feedback gate. The full pytest/C/SELinux
acceptance suite remains mandatory and must not be replaced by this script.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from alice_mouse.security import (
    AuthenticatedPrincipal,
    GrantError,
    InputGrant,
    ProtectedVerifier,
    SessionAuthority,
    TrustedSigner,
)
from alice_mouse.security.grants import TTL_NS
from alice_mouse.security.socket_boundary import SignedInputSocket, SocketBoundaryError

BUDGET_SECONDS = 2.0


def rejected(fn) -> None:
    try:
        fn()
    except GrantError:
        return
    raise AssertionError("unsafe command was accepted")


def session(*, dispatch=None):
    clock = [1_000_000_000]
    owner = AuthenticatedPrincipal("owner", "test-device", "controller")
    authority = SessionAuthority(
        "owner",
        "test-device",
        clock=lambda: clock[0],
        identity_verifier=lambda identity: identity is owner,
    )
    authority.authorize(owner)
    key = bytes(range(32))
    signer = TrustedSigner(authority, key)
    recorded = []
    verifier = ProtectedVerifier(
        key,
        "owner",
        "test-device",
        clock=lambda: clock[0],
        dispatch=dispatch or (lambda action, payload: recorded.append((action, payload)) or True),
    )
    verifier.provision(authority, owner)
    return clock, owner, authority, signer, verifier, recorded


def verify_signed_protocol() -> int:
    clock, owner, authority, signer, verifier, recorded = session()
    action = InputGrant("move", {"x": 1, "y": -1})
    packet = signer.sign(owner, action)
    assert verifier.accept(packet) is True
    rejected(lambda: verifier.accept(packet))  # replay
    rejected(
        lambda: signer.sign(AuthenticatedPrincipal("owner", "test-device", "controller"), action)
    )

    tampered = json.loads(signer.sign(owner, action))
    tampered["grant"]["payload"]["x"] = 400
    rejected(lambda: verifier.accept(json.dumps(tampered).encode("utf-8")))

    bad_key = signer.sign(owner, InputGrant("key_down", {"key": 116}))
    rejected(lambda: verifier.accept(bad_key))  # KEY_POWER even when HMAC is valid

    second = signer.sign(owner, action)
    third = signer.sign(owner, action)
    assert verifier.accept(third)
    rejected(lambda: verifier.accept(second))  # out-of-order sequence

    timed = signer.sign(owner, action)
    clock[0] += TTL_NS + 1
    rejected(lambda: verifier.accept(timed))  # expired signature
    assert len(recorded) == 2
    authority.revoke()
    rejected(lambda: signer.sign(owner, action))
    return 8


def verify_backend_failure() -> int:
    called = []

    def faulty(action, payload):
        called.append((action, payload))
        raise OSError("emulated device write failed")

    _, owner, _, signer, verifier, _ = session(dispatch=faulty)
    first = signer.sign(owner, InputGrant("key_down", {"key": 30}))
    rejected(lambda: verifier.accept(first))
    assert verifier.session is None
    second = signer.sign(owner, InputGrant("key_up", {"key": 30}))
    rejected(lambda: verifier.accept(second))
    assert len(called) == 1
    return 2


def verify_queued_ttl() -> int:
    clock, owner, _, signer, verifier, recorded = session()
    packet = signer.sign(owner, InputGrant("move", {"x": 0, "y": 0}))
    seen = threading.Event()
    continue_after_expiry = threading.Event()
    first = [True]
    results = []

    def verifier_clock():
        if threading.current_thread() is worker and first[0]:
            first[0] = False
            sampled = clock[0]
            seen.set()
            assert continue_after_expiry.wait(0.5)
            return sampled
        return clock[0]

    verifier.clock = verifier_clock

    def attempt():
        try:
            verifier.accept(packet)
            results.append("accepted")
        except GrantError:
            results.append("denied")

    with verifier.lock:
        worker = threading.Thread(target=attempt, daemon=True)
        worker.start()
        assert seen.wait(0.5)
        clock[0] += TTL_NS + 1
        continue_after_expiry.set()

    worker.join(timeout=0.5)
    assert not worker.is_alive()
    assert results == ["denied"] and recorded == [] and verifier.seq == 0
    return 1


def verify_unix_boundary() -> int:
    _, owner, _, signer, verifier, recorded = session()
    with tempfile.TemporaryDirectory(prefix="ai-fast-", dir=os.environ.get("TMPDIR")) as root:
        directory = Path(root)
        os.chmod(directory, 0o700)
        endpoint = SignedInputSocket(directory / "i.sock", verifier, peer_uid=os.getuid())
        endpoint.start()
        try:
            for wire, expected in (
                (b"key_down 30", b"DENIED\n"),
                (signer.sign(owner, InputGrant("key_down", {"key": 30})), b"OK\n"),
            ):
                with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as conn:
                    conn.settimeout(0.8)
                    conn.connect(str(endpoint.path))
                    conn.sendall(wire)
                    assert conn.recv(128) == expected
        finally:
            endpoint.stop()
        assert not endpoint.path.exists()
    assert recorded == [("key_down", {"key": 30})]
    return 3


def verify_bind_swap() -> int:
    with tempfile.TemporaryDirectory(prefix="ai-race-", dir=os.environ.get("TMPDIR")) as root:
        directory = Path(root)
        original_dir = directory / "old"
        alternate_dir = directory / "alternate"
        moved_dir = directory / "moved"
        original_dir.mkdir(mode=0o700)
        alternate_dir.mkdir(mode=0o700)
        _, _, _, _, verifier, _ = session()
        endpoint = SignedInputSocket(original_dir / "i.sock", verifier, peer_uid=os.getuid())
        bind = socket.socket.bind

        def replace_on_bind(server, path):
            if str(path).endswith("/i.sock"):
                original_dir.rename(moved_dir)
                alternate_dir.rename(original_dir)
            return bind(server, path)

        with patch.object(socket.socket, "bind", replace_on_bind):
            try:
                endpoint.start()
            except SocketBoundaryError:
                pass
            else:
                endpoint.stop()
                raise AssertionError("directory swap was accepted")

        assert endpoint._parent_fd is None
        assert not (original_dir / "i.sock").exists()
        assert not (moved_dir / "i.sock").exists()
    return 3


def verify_directory_fd_cleanup() -> int:
    with tempfile.TemporaryDirectory(prefix="ai-fd-", dir=os.environ.get("TMPDIR")) as root:
        directory = Path(root)
        parent = directory / "old"
        moved = directory / "moved"
        parent.mkdir(mode=0o700)
        _, _, _, _, verifier, _ = session()
        endpoint = SignedInputSocket(parent / "i.sock", verifier, peer_uid=os.getuid())
        open_fd = os.open
        captured = []

        def disappear(path, flags, *args, **kwargs):
            fd = open_fd(path, flags, *args, **kwargs)
            if os.fspath(path) == os.fspath(parent):
                captured.append(fd)
                parent.rename(moved)
            return fd

        with patch("os.open", side_effect=disappear):
            try:
                endpoint.start()
            except FileNotFoundError:
                pass
            else:
                endpoint.stop()
                raise AssertionError("removed parent directory was accepted")

        assert captured
        try:
            os.fstat(captured[0])
        except OSError:
            pass
        else:
            raise AssertionError("directory descriptor leaked")
    return 2


def main() -> int:
    if not __debug__:
        raise RuntimeError("fast gate requires assertions enabled")
    start = time.perf_counter()
    checks = (
        verify_signed_protocol()
        + verify_backend_failure()
        + verify_queued_ttl()
        + verify_unix_boundary()
        + verify_bind_swap()
        + verify_directory_fd_cleanup()
    )
    elapsed = time.perf_counter() - start
    assert elapsed <= BUDGET_SECONDS, (
        f"2s fast gate exceeded: {elapsed:.3f}s; do not skip any security emulations"
    )
    print(f"ALICE_INPUT_FAST PASS {checks} checks {elapsed:.3f}s (budget {BUDGET_SECONDS:.1f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
