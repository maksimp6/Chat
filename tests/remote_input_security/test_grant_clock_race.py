"""TOCTOU regression: a queued signed grant cannot outlive its TTL."""

import secrets
import threading

from remote_input.security import (
    AuthenticatedPrincipal,
    GrantError,
    InputGrant,
    ProtectedVerifier,
    SessionAuthority,
    TrustedSigner,
)
from remote_input.security.grants import TTL_NS


def test_expired_grant_denied_after_waiting_for_verifier_dispatch_lock():
    now = [1_000_000_000]
    principal = AuthenticatedPrincipal("owner", "redmi9", "controller")
    authority = SessionAuthority(
        "owner",
        "redmi9",
        clock=lambda: now[0],
        identity_verifier=lambda identity: identity is principal,
    )
    authority.authorize(principal)
    key = secrets.token_bytes(32)
    signer = TrustedSigner(authority, key)
    packet = signer.sign(principal, InputGrant("move", {"x": 1, "y": 0}))

    clock_checked = threading.Event()
    resume_after_expiry = threading.Event()
    first_worker_clock = [True]
    dispatched = []
    results = []

    def verifier_clock():
        if threading.current_thread() is worker and first_worker_clock[0]:
            first_worker_clock[0] = False
            sampled = now[0]
            clock_checked.set()
            assert resume_after_expiry.wait(3)
            return sampled
        return now[0]

    verifier = ProtectedVerifier(
        key,
        "owner",
        "redmi9",
        clock=verifier_clock,
        dispatch=lambda action, payload: dispatched.append((action, payload)) or True,
    )
    verifier.provision(authority, principal)

    def process_packet():
        try:
            results.append(verifier.accept(packet))
        except GrantError:
            results.append("denied")

    with verifier.lock:
        worker = threading.Thread(target=process_packet, daemon=True)
        worker.start()
        assert clock_checked.wait(2)
        now[0] += TTL_NS + 1
        resume_after_expiry.set()

    worker.join(3)
    assert not worker.is_alive()
    assert results == ["denied"], "expired packet was dispatched after a lock wait"
    assert dispatched == [], "a physically expired grant must cause no effects"
    assert verifier.seq == 0, "failed TTL check cannot consume replay sequence"
