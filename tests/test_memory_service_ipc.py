"""Bounded IPC contract tests without privileged runner."""

import socket

import pytest

from memory_engine.service import MAX_REQUEST, receive


def test_ipc_rejects_oversized_request():
    server, client = socket.socketpair()
    try:
        client.sendall(b"x" * (MAX_REQUEST + 1))
        with pytest.raises(ValueError, match="invalid request length"):
            receive(server)
    finally:
        server.close()
        client.close()


def test_ipc_rejects_incomplete_frame():
    server, client = socket.socketpair()
    try:
        client.sendall(b'{"op":"info"}')
        client.shutdown(socket.SHUT_WR)
        with pytest.raises(ValueError, match="invalid request length"):
            receive(server)
    finally:
        server.close()
        client.close()


def test_ipc_rejects_non_object():
    server, client = socket.socketpair()
    try:
        client.sendall(b'[]\n')
        with pytest.raises(ValueError, match="invalid request"):
            receive(server)
    finally:
        server.close()
        client.close()


def test_ipc_timeout_does_not_commit(tmp_path, monkeypatch):
    import memory_engine.service as service
    from memory_engine import MemoryStore

    server, client = socket.socketpair()
    try:
        monkeypatch.setattr(service, "peer_uid", lambda _conn: 12345)

        def interrupted(_conn):
            raise TimeoutError("injected stalled client")

        monkeypatch.setattr(service, "receive", interrupted)
        with MemoryStore(tmp_path / "db.memory") as store:
            service.handle_client(server, store, 12345)
            assert store.last_commit.sequence == 0
    finally:
        server.close()
        client.close()


def test_ipc_unauthorized_peer_never_reads_request(tmp_path, monkeypatch):
    import memory_engine.service as service
    from memory_engine import MemoryStore

    server, client = socket.socketpair()
    try:
        monkeypatch.setattr(service, "peer_uid", lambda _conn: 99999)

        def unexpected(_conn):
            raise AssertionError("unauthorized request was read")

        monkeypatch.setattr(service, "receive", unexpected)
        with MemoryStore(tmp_path / "db.memory") as store:
            service.handle_client(server, store, 12345)
            assert store.last_commit.sequence == 0
    finally:
        server.close()
        client.close()
