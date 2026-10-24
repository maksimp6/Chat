"""Socket-level TTL TOCTOU gate (synthetic backend, no physical input).

A grant parsed before an earlier dispatch releases its lock may be expired by
the time it reaches the protected backend. Kernel SO_PEERCRED is used normally.
"""

from __future__ import annotations

import os
from pathlib import Path
import secrets
import shutil
import socket
import tempfile
import threading

from alice_mouse.security import (
    AuthenticatedPrincipal,
    InputGrant,
    ProtectedVerifier,
    SessionAuthority,
    TrustedSigner,
)
from alice_mouse.security.grants import TTL_NS
from alice_mouse.security.socket_boundary import SignedInputSocket


def test_grant_expiring_behind_socket_dispatch_lock_is_denied():
    now = [1_000_000_000]
    principal = AuthenticatedPrincipal("owner", "redmi9", "controller")
    authority = SessionAuthority(
        "owner",
        "redmi9",
        clock=lambda: now[0],
        identity_verifier=lambda user: user is principal,
    )
    authority.authorize(principal)
    key = secrets.token_bytes(32)
    packet = TrustedSigner(authority, key).sign(principal, InputGrant("move", {"x": 4, "y": -2}))

    sampled_before_lock = threading.Event()
    release_sample = threading.Event()
    capture_once = [True]
    events: list[tuple[str, dict]] = []
    received: list[bytes] = []

    def verifier_clock():
        # Freeze the first socket-handler clock result before the dispatch lock.
        # Later calls must observe the advanced monotonic time.
        if threading.current_thread() is endpoint._thread and capture_once[0]:
            capture_once[0] = False
            sampled = now[0]
            sampled_before_lock.set()
            assert release_sample.wait(3)
            return sampled
        return now[0]

    verifier = ProtectedVerifier(
        key,
        "owner",
        "redmi9",
        clock=verifier_clock,
        dispatch=lambda action, payload: events.append((action, payload)) or True,
    )
    verifier.provision(authority, principal)

    directory = Path(tempfile.mkdtemp(prefix="ai-", dir=os.environ.get("TMPDIR")))
    os.chmod(directory, 0o700)
    endpoint = SignedInputSocket(directory / "i.sock", verifier, peer_uid=os.getuid())

    def send_signed_packet():
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as conn:
                conn.settimeout(3)
                conn.connect(str(endpoint.path))
                conn.sendall(packet)
                received.append(conn.recv(64))
        except OSError:
            received.append(b"SOCKET_ERROR")

    endpoint.start()
    worker = threading.Thread(target=send_signed_packet, daemon=True)
    try:
        with verifier.lock:
            worker.start()
            assert sampled_before_lock.wait(2), "packet did not reach verifier clock"
            now[0] += TTL_NS + 1
            release_sample.set()
        worker.join(3)
        assert not worker.is_alive(), "client must never hang after TTL expiry"
        assert received == [b"DENIED\n"], "stale grant crossed the protected lock"
        assert events == [], "expired grant produced a simulated input effect"
        assert verifier.seq == 0, "invalid grant consumed a replay sequence"
    finally:
        release_sample.set()
        endpoint.stop()
        worker.join(2)
        shutil.rmtree(directory)
