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
        client.sendall(b"[]\n")
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


def test_stalled_ipc_worker_releases_capacity(tmp_path, monkeypatch):
    import threading
    import memory_engine.service as service
    from memory_engine import MemoryStore

    server, client = socket.socketpair()
    slots = threading.BoundedSemaphore(1)
    assert slots.acquire(blocking=False)
    try:
        monkeypatch.setattr(service, "peer_uid", lambda _conn: 12345)

        def timeout(_conn):
            raise TimeoutError("fault-injected stalled peer")

        monkeypatch.setattr(service, "receive", timeout)
        with MemoryStore(tmp_path / "db.memory") as store:
            service._serve_one(server, store, 12345, slots)
            assert slots.acquire(blocking=False)
            slots.release()
            assert store.last_commit.sequence == 0
    finally:
        server.close()
        client.close()


def test_concurrent_ipc_commits_are_individually_acknowledged(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import json
    import memory_engine.service as service
    from memory_engine import MemoryStore

    monkeypatch.setattr(service, "peer_uid", lambda _conn: 12345)
    path = tmp_path / "db.memory"
    with MemoryStore(path) as store:
        def worker(index):
            server, client = socket.socketpair()
            try:
                payload = {"op": "set", "key": f"k{index}", "value": index}
                client.sendall((json.dumps(payload) + "\n").encode())
                service.handle_client(server, store, 12345)
                return json.loads(client.recv(4096))
            finally:
                server.close()
                client.close()

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(worker, range(12)))
        assert all(result["ok"] for result in results)
        assert sorted(result["sequence"] for result in results) == list(range(1, 13))
    with MemoryStore(path) as reopened:
        assert all(reopened.get(f"k{i}") == i for i in range(12))
        assert reopened.last_commit.sequence == 12


@pytest.mark.skipif(not hasattr(__import__("signal"), "SIGKILL"), reason="POSIX SIGKILL required")
def test_sigkill_after_ack_replays_committed_data(tmp_path):
    import os
    import signal
    import subprocess
    import sys
    from memory_engine import MemoryStore

    path = tmp_path / "db.memory"
    code = (
        "import sys,time; from memory_engine import MemoryStore; "
        "s=MemoryStore(sys.argv[1]); s.set('durable',17); "
        "s.commit(); print('ACK',flush=True); time.sleep(30)"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", code, str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None
        assert process.stdout.readline().strip() == "ACK"
        os.kill(process.pid, signal.SIGKILL)
        process.wait(timeout=5)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
    with MemoryStore(path) as reopened:
        assert reopened.get("durable") == 17
        assert reopened.last_commit.sequence == 1


def test_ipc_dos_capacity_is_bounded(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    import memory_engine.service as service
    from memory_engine import MemoryStore

    slots = threading.BoundedSemaphore(service.MAX_INFLIGHT)
    release = threading.Event()
    entered = threading.Barrier(service.MAX_INFLIGHT + 1)
    monkeypatch.setattr(service, "peer_uid", lambda _conn: 12345)

    def stalled(_conn):
        entered.wait(timeout=5)
        release.wait(timeout=5)
        raise TimeoutError("fault injected slow client")

    monkeypatch.setattr(service, "receive", stalled)
    pairs = [socket.socketpair() for _ in range(service.MAX_INFLIGHT)]
    try:
        with MemoryStore(tmp_path / "db.memory") as store:
            with ThreadPoolExecutor(max_workers=service.MAX_INFLIGHT) as pool:
                futures = []
                for server, _client in pairs:
                    assert slots.acquire(blocking=False)
                    futures.append(
                        pool.submit(service._serve_one, server, store, 12345, slots)
                    )
                entered.wait(timeout=5)
                assert not slots.acquire(blocking=False)
                release.set()
                for future in futures:
                    future.result(timeout=5)
            assert slots.acquire(blocking=False)
            slots.release()
            assert store.last_commit.sequence == 0
    finally:
        release.set()
        for server, client in pairs:
            server.close()
            client.close()
