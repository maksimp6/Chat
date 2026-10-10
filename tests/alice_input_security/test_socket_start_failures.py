"""Isolated lifecycle tests: no root privileges or Android input used."""
import pytest
from alice_mouse.security.socket_boundary import SocketBoundaryError
from test_socket_boundary import harness


def test_worker_start_failure_does_not_leave_socket(harness, monkeypatch):
    import alice_mouse.security.socket_boundary as module
    _, _, _, _, _, endpoint, _ = harness
    original = module.threading.Thread.start
    def fail(self):
        raise RuntimeError('injected thread failure')
    monkeypatch.setattr(module.threading.Thread, 'start', fail)
    with pytest.raises(RuntimeError, match='injected thread failure'):
        endpoint.start()
    assert not endpoint.path.exists()
    assert endpoint._sock is None
    assert endpoint._parent_fd is None
    monkeypatch.setattr(module.threading.Thread, 'start', original)
    endpoint.start()
    endpoint.stop()


def test_second_start_does_not_replace_socket(harness):
    _, _, _, _, _, endpoint, _ = harness
    endpoint.start()
    first = endpoint.path.stat().st_ino
    with pytest.raises(SocketBoundaryError):
        endpoint.start()
    assert endpoint.path.stat().st_ino == first
