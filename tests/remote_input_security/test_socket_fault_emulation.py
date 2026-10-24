"""Fault-injection tests: emulate races and failures without Android input."""

import os
import socket
import tempfile
from pathlib import Path

import pytest

from remote_input.security.socket_boundary import SignedInputSocket, SocketBoundaryError


class FakeVerifier:
    def __init__(self):
        self.shutdown = False
        self.revoked = False

    def request_shutdown(self):
        self.shutdown = True

    def revoke(self):
        self.revoked = True

    def accept(self, packet):
        return False


def test_bind_failure_leaves_no_socket_or_open_descriptor(tmp_path, monkeypatch):
    os.chmod(tmp_path, 0o700)
    verifier = FakeVerifier()
    endpoint = SignedInputSocket(tmp_path / "input.sock", verifier, peer_uid=os.getuid())
    original_bind = socket.socket.bind

    def reject_bind(sock, address):
        if address.endswith("/input.sock"):
            raise OSError("emulated bind failure")
        return original_bind(sock, address)

    monkeypatch.setattr(socket.socket, "bind", reject_bind)
    with pytest.raises(OSError, match="emulated bind failure"):
        endpoint.start()
    assert endpoint._parent_fd is None
    assert endpoint._sock is None
    assert not endpoint.path.exists()


@pytest.fixture
def short_tmp():
    with tempfile.TemporaryDirectory(prefix="ai-", dir=os.environ.get("TMPDIR")) as directory:
        yield Path(directory)


def test_parent_swap_during_bind_fails_closed(short_tmp, monkeypatch):
    tmp_path = short_tmp
    parent = tmp_path / "owned"
    parent.mkdir(mode=0o700)
    replacement = tmp_path / "replacement"
    replacement.mkdir(mode=0o700)
    verifier = FakeVerifier()
    endpoint = SignedInputSocket(parent / "input.sock", verifier, peer_uid=os.getuid())
    original_bind = socket.socket.bind

    def swap_parent(sock, address):
        if address.endswith("/input.sock"):
            parent.rename(tmp_path / "moved")
            replacement.rename(parent)
        return original_bind(sock, address)

    monkeypatch.setattr(socket.socket, "bind", swap_parent)
    with pytest.raises(SocketBoundaryError, match="parent replaced"):
        endpoint.start()
    assert endpoint._parent_fd is None
    assert endpoint._sock is None
    assert not (parent / "input.sock").exists()
    # Even an adversarial path replacement cannot leave a socket in either tree.
    assert not (tmp_path / "moved" / "input.sock").exists()


def test_disappearing_parent_does_not_leak_open_fd(tmp_path, monkeypatch):
    """Emulate directory removal between open(dirfd) and pathname verification."""
    import remote_input.security.socket_boundary as module

    os.chmod(tmp_path, 0o700)
    endpoint = SignedInputSocket(tmp_path / "input.sock", FakeVerifier(), peer_uid=os.getuid())
    moved = tmp_path.with_name(tmp_path.name + "-moved")
    original_open = os.open
    opened = []

    def disappear(path, flags, *args, **kwargs):
        fd = original_open(path, flags, *args, **kwargs)
        if os.fspath(path) == os.fspath(tmp_path):
            opened.append(fd)
            tmp_path.rename(moved)
        return fd

    monkeypatch.setattr(module.os, "open", disappear)
    try:
        with pytest.raises(FileNotFoundError):
            endpoint.start()
        assert opened
        with pytest.raises(OSError):
            os.fstat(opened[0])
        assert endpoint._sock is None
    finally:
        if moved.exists():
            moved.rename(tmp_path)
