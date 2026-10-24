"""Provisioning must not resurrect or replay a previously issued epoch.

All effects are in-memory; never use a real input device.
"""

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from remote_input.security import (
    AuthenticatedPrincipal,
    GrantError,
    InputGrant,
    ProtectedVerifier,
    SessionAuthority,
    TrustedSigner,
)


@pytest.fixture
def signed_session():
    tick = [1_000_000_000]
    principal = AuthenticatedPrincipal("owner", "redmi9", "controller")
    authority = SessionAuthority(
        "owner",
        "redmi9",
        clock=lambda: tick[0],
        identity_verifier=lambda candidate: candidate is principal,
    )
    authority.authorize(principal)
    key = bytes(range(32))
    signer = TrustedSigner(authority, key)
    effects = []
    deny_backend = [False]

    def dispatch(action, payload):
        effects.append((action, payload))
        return not deny_backend[0]

    def verifier():
        return ProtectedVerifier(key, "owner", "redmi9", clock=lambda: tick[0], dispatch=dispatch)

    return tick, principal, authority, signer, verifier, effects, deny_backend


def test_duplicate_provision_cannot_reset_sequence_or_replay(signed_session):
    _, principal, authority, signer, make_verifier, effects, _ = signed_session
    verifier = make_verifier()
    verifier.provision(authority, principal)
    packet = signer.sign(principal, InputGrant("key_down", {"key": 30}))
    assert verifier.accept(packet)
    assert verifier.seq == 1

    with pytest.raises(GrantError, match="provision|issued|session|epoch"):
        verifier.provision(authority, principal)
    assert verifier.seq == 1
    with pytest.raises(GrantError):
        verifier.accept(packet)
    assert len(effects) == 1

    packet2 = signer.sign(principal, InputGrant("key_up", {"key": 30}))
    assert verifier.accept(packet2)
    assert len(effects) == 2


def test_failed_backend_cannot_reprovision_same_epoch(signed_session):
    _, principal, authority, signer, make_verifier, effects, deny = signed_session
    verifier = make_verifier()
    verifier.provision(authority, principal)
    old = signer.sign(principal, InputGrant("move", {"x": 1, "y": 1}))
    assert verifier.accept(old)
    deny[0] = True
    with pytest.raises(GrantError, match="denied|revoked|backend"):
        verifier.accept(signer.sign(principal, InputGrant("key_down", {"key": 30})))
    assert verifier.session is None

    with pytest.raises(GrantError, match="provision|issued|session|epoch"):
        verifier.provision(authority, principal)
    assert verifier.session is None

    deny[0] = False
    authority.authorize(principal)
    verifier.provision(authority, principal)
    with pytest.raises(GrantError):
        verifier.accept(old)
    new_packet = signer.sign(principal, InputGrant("move", {"x": 2, "y": 2}))
    assert verifier.accept(new_packet)
    assert len(effects) == 3


def test_new_verifier_instance_must_not_reuse_consumed_session(signed_session):
    _, principal, authority, signer, make_verifier, effects, _ = signed_session
    old_verifier = make_verifier()
    old_verifier.provision(authority, principal)
    old = signer.sign(principal, InputGrant("key_down", {"key": 29}))
    assert old_verifier.accept(old)
    old_verifier.request_shutdown()
    old_verifier.revoke()

    restart = make_verifier()
    with pytest.raises(GrantError, match="provision|issued|session|epoch"):
        restart.provision(authority, principal)
    assert restart.session is None
    assert len(effects) == 1

    authority.authorize(principal)
    restart.provision(authority, principal)
    with pytest.raises(GrantError):
        restart.accept(old)
    fresh = signer.sign(principal, InputGrant("key_up", {"key": 29}))
    assert restart.accept(fresh)
    assert len(effects) == 2


def test_concurrent_verifiers_cannot_claim_identical_epoch(signed_session):
    _, principal, authority, _, make_verifier, _, _ = signed_session
    first, second = make_verifier(), make_verifier()
    barrier = threading.Barrier(2)

    def claim(verifier):
        barrier.wait(timeout=3)
        try:
            verifier.provision(authority, principal)
            return True
        except GrantError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(claim, (first, second)))
    assert sorted(outcomes) == [False, True]
    assert sum(verifier.session is not None for verifier in (first, second)) == 1


def test_rejected_handoff_does_not_consume_authority_epoch(signed_session, monkeypatch):
    _, principal, authority, _, make_verifier, _, _ = signed_session
    verifier = make_verifier()
    checks = [0]
    active = authority.active

    def revoke_between_checks(candidate):
        checks[0] += 1
        return checks[0] == 1 and active(candidate)

    monkeypatch.setattr(authority, "active", revoke_between_checks)
    with pytest.raises(GrantError):
        verifier.provision(authority, principal)
    monkeypatch.setattr(authority, "active", active)
    verifier.provision(authority, principal)
    assert verifier.session == authority.session


def test_lost_session_cannot_be_claimed_even_with_stale_positive_checker(
    signed_session, monkeypatch
):
    """Do not publish a None session when an identity callback races state."""
    _, principal, authority, _, make_verifier, _, _ = signed_session
    authority.session = None
    monkeypatch.setattr(authority, "active", lambda _principal: True)
    verifier = make_verifier()
    with pytest.raises(GrantError, match="controller session no longer active"):
        verifier.provision(authority, principal)
    assert verifier.session is None
    assert authority._claimed_provision is None


def test_signed_unix_socket_denies_replay_after_duplicate_provision(signed_session):
    """Real SOCK_SEQPACKET transport must deny an already accepted key-down."""
    import os
    import socket
    import tempfile
    from pathlib import Path

    from remote_input.security.socket_boundary import SignedInputSocket

    _, principal, authority, signer, make_verifier, effects, _ = signed_session
    verifier = make_verifier()
    verifier.provision(authority, principal)
    payload = signer.sign(principal, InputGrant("key_down", {"key": 30}))
    with tempfile.TemporaryDirectory(prefix="ai-", dir=os.environ.get("TMPDIR")) as tmp:
        folder = Path(tmp)
        os.chmod(folder, 0o700)
        service = SignedInputSocket(folder / "i.sock", verifier, peer_uid=os.getuid())
        service.start()
        try:

            def send(packet):
                with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as client:
                    client.settimeout(2)
                    client.connect(str(service.path))
                    client.sendall(packet)
                    return client.recv(64)

            assert send(payload) == b"OK\n"
            with pytest.raises(GrantError, match="session already provisioned"):
                verifier.provision(authority, principal)
            assert send(payload) == b"DENIED\n"
            assert len(effects) == 1
        finally:
            service.stop()


def test_process_recovery_with_fresh_epoch_denies_old_signed_socket_packet(signed_session):
    """After a verifier restart, only a fresh authority epoch can be issued."""
    import os
    import socket
    import tempfile
    from pathlib import Path

    from remote_input.security.socket_boundary import SignedInputSocket

    _, principal, authority, signer, make_verifier, effects, _ = signed_session
    verifier = make_verifier()
    verifier.provision(authority, principal)
    previously_issued = signer.sign(principal, InputGrant("key_down", {"key": 30}))
    assert verifier.accept(previously_issued)
    verifier.request_shutdown()
    verifier.revoke()

    restarted = make_verifier()
    with pytest.raises(GrantError, match="session already provisioned"):
        restarted.provision(authority, principal)
    authority.authorize(principal)
    restarted.provision(authority, principal)
    with tempfile.TemporaryDirectory(prefix="ai-", dir=os.environ.get("TMPDIR")) as tmp:
        folder = Path(tmp)
        os.chmod(folder, 0o700)
        service = SignedInputSocket(folder / "i.sock", restarted, peer_uid=os.getuid())
        service.start()
        try:

            def send(packet):
                with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as client:
                    client.settimeout(2)
                    client.connect(str(service.path))
                    client.sendall(packet)
                    return client.recv(64)

            assert send(previously_issued) == b"DENIED\n"
            fresh = signer.sign(principal, InputGrant("key_up", {"key": 30}))
            assert send(fresh) == b"OK\n"
            assert len(effects) == 2
        finally:
            service.stop()
