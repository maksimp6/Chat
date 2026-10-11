"""Regression tests for descriptor-pinned socket directory policy (no uinput)."""
import os
import stat
from pathlib import Path

import pytest

from alice_mouse.security.socket_boundary import SignedInputSocket, SocketBoundaryError


class DenyVerifier:
    def accept(self, packet):
        return False

    def request_shutdown(self):
        pass

    def revoke(self):
        pass


def test_private_socket_directory_starts_and_cleans_up(tmp_path):
    os.chmod(tmp_path, 0o700)
    endpoint = SignedInputSocket(tmp_path / "input.sock", DenyVerifier(), peer_uid=os.getuid())
    endpoint.start()
    try:
        assert stat.S_ISSOCK((tmp_path / "input.sock").lstat().st_mode)
        assert stat.S_IMODE((tmp_path / "input.sock").stat().st_mode) == 0o600
    finally:
        endpoint.stop()
    assert not (tmp_path / "input.sock").exists()


@pytest.mark.parametrize("mode", [0o777, 0o750, 0o710])
def test_rejects_unsafe_private_directory_modes(tmp_path, mode):
    os.chmod(tmp_path, mode)
    endpoint = SignedInputSocket(tmp_path / "input.sock", DenyVerifier(), peer_uid=os.getuid())
    with pytest.raises(SocketBoundaryError):
        endpoint.start()
    assert not (tmp_path / "input.sock").exists()


def test_rejects_symlinked_parent(tmp_path):
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    endpoint = SignedInputSocket(link / "input.sock", DenyVerifier(), peer_uid=os.getuid())
    with pytest.raises(OSError):
        endpoint.start()
    assert not (real / "input.sock").exists()
