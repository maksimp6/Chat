"""Injected boundary failures: each denial preserves the no-input invariant.

No /dev/uinput, Android input, root commands or external packets are used.
"""

from __future__ import annotations

import hmac
import json
import os
import socket
import stat
import struct
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_socket_boundary import send

import alice_mouse.security.socket_boundary as boundary
from alice_mouse.security import (
    AuthenticatedPrincipal,
    GrantError,
    InputGrant,
    ProtectedVerifier,
    SessionAuthority,
    TrustedSigner,
    grants,
)


@pytest.fixture
def signed_setup(tmp_path):
    os.chmod(tmp_path, 0o700)
    principal = AuthenticatedPrincipal("owner", "redmi9", "controller")
    authority = SessionAuthority("owner", "redmi9", identity_verifier=lambda p: p is principal)
    authority.authorize(principal)
    key = b"k" * 32
    signer = TrustedSigner(authority, key)
    calls = []
    verifier = ProtectedVerifier(
        key, "owner", "redmi9", dispatch=lambda a, p: calls.append((a, p)) or True
    )
    endpoint = boundary.SignedInputSocket(tmp_path / "input.sock", verifier, peer_uid=os.getuid())
    yield tmp_path, principal, authority, signer, verifier, endpoint, calls
    endpoint.stop()


def test_invalid_identity_and_keys_fail_before_handshake():
    with pytest.raises(GrantError, match="owner/device required"):
        SessionAuthority("", "device")
    principal = AuthenticatedPrincipal("owner", "device", "controller")
    authority = SessionAuthority("owner", "device", identity_verifier=lambda p: p is principal)
    authority.authorize(principal)
    with pytest.raises(GrantError, match="32-byte HMAC key"):
        TrustedSigner(authority, b"x")
    with pytest.raises(GrantError, match="invalid verifier key"):
        ProtectedVerifier(b"x", "owner", "device")


def test_signer_rejects_bad_grant_payload_and_raced_revocation(monkeypatch):
    principal = AuthenticatedPrincipal("owner", "device", "controller")
    authority = SessionAuthority("owner", "device", identity_verifier=lambda p: p is principal)
    authority.authorize(principal)
    signer = TrustedSigner(authority, b"k" * 32)
    with pytest.raises(GrantError, match="unsupported action"):
        signer.sign(principal, InputGrant("root_shell", {}))
    with pytest.raises(GrantError, match="payload must be object"):
        signer.sign(principal, InputGrant("move", None))
    original_active = authority.active
    calls = 0

    def revoke_mid_sign(p):
        nonlocal calls
        calls += 1
        return original_active(p) if calls == 1 else False

    monkeypatch.setattr(authority, "active", revoke_mid_sign)
    with pytest.raises(GrantError, match="session revoked"):
        signer.sign(principal, InputGrant("move", {"x": 0, "y": 0}))


def test_packet_length_and_action_schema_rejections(monkeypatch):
    principal = AuthenticatedPrincipal("owner", "device", "controller")
    authority = SessionAuthority("owner", "device", identity_verifier=lambda p: p is principal)
    authority.authorize(principal)
    signer = TrustedSigner(authority, b"k" * 32)
    monkeypatch.setattr(grants, "MAX_PACKET", 10)
    with pytest.raises(GrantError, match="packet too large"):
        signer.sign(principal, InputGrant("move", {"x": 0, "y": 0}))
    with pytest.raises(GrantError, match="invalid button"):
        grants._validate_action_payload("down", {"button": 3})
    with pytest.raises(GrantError, match="invalid payload"):
        grants._validate_action_payload("move", [])
    with pytest.raises(GrantError, match="unsupported action"):
        grants._validate_action_payload("shell", {})


def test_verifier_rejects_shutdown_invalid_size_shape_and_version(signed_setup):
    _, principal, _, signer, verifier, _, calls = signed_setup
    packet = signer.sign(principal, InputGrant("move", {"x": 0, "y": 0}))
    verifier.request_shutdown()
    with pytest.raises(GrantError, match="verifier shut down"):
        verifier.accept(packet)
    verifier.shutdown_requested.clear()
    with pytest.raises(GrantError, match="invalid packet"):
        verifier.accept(b"")
    outer = json.loads(packet)
    del outer["grant"]["nonce"]
    with pytest.raises(GrantError, match="invalid signed grant"):
        verifier.accept(json.dumps(outer).encode())
    outer = json.loads(packet)
    outer["grant"]["v"] = 2
    outer["mac"] = hmac.digest(verifier._key, grants._canonical(outer["grant"]), "sha256").hex()
    with pytest.raises(GrantError, match="invalid signed grant"):
        verifier.accept(grants._canonical(outer))
    assert calls == []


def test_invalid_socket_metadata_peer_and_path(tmp_path):
    os.chmod(tmp_path, 0o700)
    fake = SimpleNamespace(
        st_mode=stat.S_IFDIR | 0o700,
        st_uid=os.getuid() + 1,
        st_gid=os.getgid(),
    )
    with pytest.raises(boundary.SocketBoundaryError, match="owned"):
        boundary._validate_pinned_directory(fake, None)
    for uid in (-1, "me"):
        with pytest.raises(boundary.SocketBoundaryError, match="peer UID"):
            boundary.SignedInputSocket(tmp_path / "input.sock", object(), peer_uid=uid)
    with pytest.raises(boundary.SocketBoundaryError, match="shared group"):
        boundary.SignedInputSocket(
            tmp_path / "input.sock", object(), peer_uid=os.getuid(), shared_gid=-1
        )
    endpoint = boundary.SignedInputSocket(Path(".."), object(), peer_uid=os.getuid())
    with pytest.raises(boundary.SocketBoundaryError, match="basename"):
        endpoint.start()


def test_rejects_overlong_linux_unix_socket_address(tmp_path):
    os.chmod(tmp_path, 0o700)
    endpoint = boundary.SignedInputSocket(tmp_path / ("s" * 95), object(), peer_uid=os.getuid())
    with pytest.raises(boundary.SocketBoundaryError, match="address too long"):
        endpoint.start()
    assert endpoint._parent_fd is None
    assert not endpoint.path.exists()


def test_missing_socket_after_bind_is_safe_to_unlink(tmp_path):
    os.chmod(tmp_path, 0o700)
    verifier = SimpleNamespace(request_shutdown=lambda: None, revoke=lambda: None)
    endpoint = boundary.SignedInputSocket(tmp_path / "input.sock", verifier, peer_uid=os.getuid())
    endpoint.start()
    endpoint.path.unlink()
    endpoint.stop()
    assert endpoint._parent_fd is None


def test_not_started_loop_returns_without_socket(tmp_path):
    endpoint = boundary.SignedInputSocket(tmp_path / "unused.sock", object(), peer_uid=os.getuid())
    assert endpoint._sock is None
    endpoint._run()


def test_idle_socket_timeout_emulation(tmp_path):
    """A server must survive its accept timeout before normal shutdown."""
    os.chmod(tmp_path, 0o700)
    verifier = SimpleNamespace(request_shutdown=lambda: None, revoke=lambda: None)
    endpoint = boundary.SignedInputSocket(tmp_path / "idle.sock", verifier, peer_uid=os.getuid())
    endpoint.start()
    try:
        # Real accept timeout (100 ms); we check the worker remains alive.
        import time

        time.sleep(0.23)
        assert endpoint._thread.is_alive()
    finally:
        endpoint.stop()


def test_missing_peer_credentials_deny_packet(signed_setup, monkeypatch):
    _, principal, _, signer, _, endpoint, calls = signed_setup
    endpoint.start()
    packet = signer.sign(principal, InputGrant("move", {"x": 1, "y": 1}))
    with monkeypatch.context() as mp:
        mp.delattr(socket, "SO_PEERCRED")
        try:
            denied = send(endpoint.path, packet)
        except (ConnectionResetError, BrokenPipeError):
            denied = b"DENIED\n"  # Kernel may reset an unread SOCK_SEQPACKET.
        assert denied == b"DENIED\n"
    assert calls == []


def test_denial_reply_write_failure_and_runner_stop_timeout(tmp_path):
    endpoint = boundary.SignedInputSocket(tmp_path / "mock.sock", object(), peer_uid=os.getuid())

    class FailingConn:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def settimeout(self, *_):
            pass

        def getsockopt(self, *_):
            return struct.pack("3i", os.getpid(), os.getuid(), os.getgid())

        def recv(self, *_):
            return b""  # simulated disconnect

        def sendall(self, *_):
            raise OSError("simulated write failure")

    class FakeServer:
        tries = 0

        def accept(self):
            self.tries += 1
            if self.tries == 1:
                return (FailingConn(), None)
            raise OSError("stop")

        def close(self):
            pass

    endpoint._sock = FakeServer()
    endpoint._run()  # must swallow denial reply failure and terminate cleanly
    assert endpoint._sock.tries == 2

    class StuckWorker:
        def join(self, **_):
            pass

        def is_alive(self):
            return True

    endpoint.verifier = SimpleNamespace(request_shutdown=lambda: None, revoke=lambda: None)
    endpoint._thread = StuckWorker()
    with pytest.raises(boundary.SocketBoundaryError, match="server did not stop"):
        endpoint.stop()
    assert endpoint._stop.is_set()
