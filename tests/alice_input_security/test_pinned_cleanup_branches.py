"""Review regressions for cleanup branches missed by pinned-socket tests.

No privileged sockets, root broker or real input devices are touched.
"""

import os

import pytest

from alice_mouse.security import ProtectedVerifier
from alice_mouse.security import socket_boundary as boundary


def test_socket_constructor_fault_closes_pinned_dirfd(tmp_path, monkeypatch):
    os.chmod(tmp_path, 0o700)
    verifier = ProtectedVerifier(b"k" * 32, "owner", "redmi9")
    endpoint = boundary.SignedInputSocket(tmp_path / "socket.sock", verifier, peer_uid=os.getuid())

    def failed_socket(*_args):
        raise OSError("synthetic socket allocation fault")

    with monkeypatch.context() as scope:
        scope.setattr(boundary.socket, "socket", failed_socket)
        with pytest.raises(OSError, match="synthetic socket allocation fault"):
            endpoint.start()

    assert endpoint._parent_fd is None
    assert endpoint._sock is None
    assert not endpoint.path.exists()


def test_foreign_file_at_old_socket_name_is_preserved(tmp_path):
    os.chmod(tmp_path, 0o700)
    verifier = ProtectedVerifier(b"k" * 32, "owner", "redmi9")
    endpoint = boundary.SignedInputSocket(tmp_path / "socket.sock", verifier, peer_uid=os.getuid())
    endpoint.start()
    try:
        endpoint.path.unlink()
        endpoint.path.write_text("foreign replacement")
        endpoint.stop()
        assert endpoint.path.read_text() == "foreign replacement"
        assert endpoint._parent_fd is None
    finally:
        if endpoint._sock is not None:
            endpoint.stop()
