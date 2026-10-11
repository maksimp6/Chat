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
