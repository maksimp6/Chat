"""Full-file Memory DB and real HTTP E2E for the isolated Alice Board."""

import json
import os
import signal
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from project_board.server import create_server
from project_board.store import BoardConflict, BoardError, BoardStore, BoardUnavailable

TOKEN = "test-owner-token-not-for-production-" + "a" * 24


def key(suffix: str) -> str:
    return "project-board-test:" + suffix


def test_bootstrap_persists_and_replay_is_idempotent(tmp_path):
    path = tmp_path / "project-board.memory"
    with BoardStore(path) as db:
        initial = db.threads()
        assert len(initial) == 1
        assert initial[0]["category"] == "shame"
        assert initial[0]["title"] == "Доска позора"
        topic = db.create_thread("Testing", "Initial body", "discussion", key("topic"))
        assert topic["id"] == "thread-000002"
        assert db.create_thread("Testing", "Initial body", "discussion", key("topic")) == topic
        post = db.post(topic["id"], "Original evidence", key("post"))
        assert post["id"] == "event-000003"
        assert db.post(topic["id"], "Original evidence", key("post")) == post
        assert db.export()["sequence"] == 3
        copied = db.thread(topic["id"])
        copied["posts"][0]["body"] = "tampered"
        assert db.thread(topic["id"])["posts"][0]["body"] == "Initial body"
    with BoardStore(path) as reopened:
        assert reopened.thread("thread-000002")["posts"][1]["body"] == "Original evidence"
        assert reopened.export()["sequence"] == 3
        assert reopened.post("thread-000002", "Original evidence", key("post")) == post
        assert reopened.export()["sequence"] == 3


def test_concurrent_posts_serialize_without_loss(tmp_path):
    with BoardStore(tmp_path / "db.memory") as db:
        target = db.create_thread("Concurrency", "Start", "discussion", key("multi-root"))

        def append(i):
            return db.post(target["id"], f"Reply {i}", key(f"concurrent-{i}"))

        with ThreadPoolExecutor(max_workers=8) as pool:
            replies = list(pool.map(append, range(24)))
        assert len({x["id"] for x in replies}) == 24
        assert len(db.thread(target["id"])["posts"]) == 25
        assert db.export()["sequence"] == 26


def test_idempotency_conflict_never_changes_history(tmp_path):
    with BoardStore(tmp_path / "db.memory") as db:
        db.create_thread("A", "First", "decision", key("reuse"))
        with pytest.raises(BoardConflict):
            db.create_thread("B", "Second", "decision", key("reuse"))
        assert db.export()["sequence"] == 2


def test_tasks_dependencies_owner_acceptance_and_no_merge(tmp_path):
    with BoardStore(tmp_path / "db.memory") as db:
        first = db.create_task("Build backend", [], key("task1"))
        second = db.create_task("Ship UI", [first["id"]], key("task2"))
        assert second["status"] == "blocked"
        with pytest.raises(BoardConflict, match="dependencies"):
            db.set_status(second["id"], "ready", False, key("early"))
        db.set_status(first["id"], "ready", False, key("first-ready"))
        db.set_status(first["id"], "in_progress", False, key("first-active"))
        db.set_status(first["id"], "review", False, key("first-review"))
        with pytest.raises(BoardConflict, match="acceptance"):
            db.set_status(first["id"], "done", False, key("no-acceptance"))
        finished = db.set_status(first["id"], "done", True, key("accepted"))
        assert finished["status"] == "done"
        assert finished["history"][-1]["accepted_by_owner"] is True
        assert db.set_status(first["id"], "done", True, key("accepted")) == finished
        assert db.set_status(second["id"], "ready", False, key("second-ready"))["status"] == "ready"
        proof = db.add_evidence(
            first["id"], "Test result", "https://example.test/report", key("proof")
        )
        assert proof["evidence"][0]["verified"] is False
        assert "merge" not in {event["kind"] for event in db.export()["events"]}


def test_second_writer_refused_and_released(tmp_path):
    path = tmp_path / "db.memory"
    with BoardStore(path) as db:
        with pytest.raises(BoardUnavailable, match="a writer"):
            BoardStore(path)
        assert db.threads()
    with BoardStore(path) as reopened:
        assert reopened.threads()


def test_cross_process_writer_lock(tmp_path):
    path = tmp_path / "db.memory"
    with BoardStore(path):
        program = (
            "from project_board.store import BoardStore,BoardUnavailable;"
            "import sys;"
            "p=sys.argv[1];"
            "\ntry: BoardStore(p); sys.exit(3)"
            "\nexcept BoardUnavailable: sys.exit(0)"
        )
        result = subprocess.run(
            [sys.executable, "-c", program, str(path)],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert result.returncode == 0, result.stderr


def test_failed_write_poisoned_until_reopen(tmp_path, monkeypatch):
    path = tmp_path / "db.memory"
    with BoardStore(path) as db:

        def failed_put(*_args):
            raise OSError("injected fsync failure")

        monkeypatch.setattr(db._db, "put", failed_put)
        with pytest.raises(BoardUnavailable, match="recovery required"):
            db.post("thread-000001", "not durable", key("failing"))
        with pytest.raises(BoardUnavailable, match="uncertain write"):
            db.post("thread-000001", "second write", key("failing2"))
        assert db.export()["sequence"] == 1
    with BoardStore(path) as reopened:
        assert reopened.export()["sequence"] == 1


def test_sigkill_after_ack_preserves_confirmed_event(tmp_path):
    path = tmp_path / "db.memory"
    program = (
        "import os,signal,sys;"
        "from project_board.store import BoardStore;"
        "db=BoardStore(sys.argv[1]);"
        "db.create_thread('durable','ACK was seen','discussion','alice:test:sigkill');"
        "print('ACK',flush=True);"
        "os.kill(os.getpid(),signal.SIGKILL)"
    )
    result = subprocess.run(
        [sys.executable, "-c", program, str(path)],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == -signal.SIGKILL
    assert "ACK" in result.stdout
    with BoardStore(path) as db:
        assert db.thread("thread-000002")["posts"][0]["body"] == "ACK was seen"


def test_truncated_tail_recovery_preserves_confirmed_data(tmp_path):
    path = tmp_path / "db.memory"
    with BoardStore(path) as db:
        db.create_thread("Last safe", "committed", "discussion", key("safe"))
    with path.open("ab") as writer:
        writer.write(b'{"v":1,"incomplete":')
        writer.flush()
        os.fsync(writer.fileno())
    with BoardStore(path) as reopened:
        assert reopened.thread("thread-000002")["title"] == "Last safe"
        assert reopened.export()["sequence"] == 2


def test_private_database_and_no_sql(tmp_path):
    path = tmp_path / "private" / "db.memory"
    with BoardStore(path) as db:
        assert db.export()["sequence"] == 1
        assert path.stat().st_mode & 0o077 == 0
        assert (
            "import sqlite3"
            not in (Path(__file__).resolve().parents[1] / "project_board/store.py").read_text()
        )
    path.chmod(0o644)
    with pytest.raises(BoardUnavailable, match="permissions"):
        BoardStore(path)


def test_validation_and_secure_evidence_url(tmp_path):
    with BoardStore(tmp_path / "db.memory") as db:
        with pytest.raises(BoardError):
            db.create_thread(" ", "text", "discussion", key("bad"))
        task = db.create_task("Inspect logs", [], key("inspect"))
        with pytest.raises(BoardError, match="invalid evidence URL"):
            db.add_evidence(task["id"], "Evidence", "file:///etc/passwd", key("bad-url"))
        with pytest.raises(BoardError):
            db.create_task("Bad dep", ["task-999999"], key("bad-dep"))


@pytest.fixture
def board_http(tmp_path):
    store = BoardStore(tmp_path / "http.memory")
    server = create_server(store, TOKEN, port=0)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", store
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        store.close()


def http(base, method, route, payload=None, token=TOKEN, raw=None):
    headers = {}
    if token is not None:
        headers["Authorization"] = "Bearer " + token
    if payload is not None or raw is not None:
        headers["Content-Type"] = "application/json"
    body = (
        raw
        if raw is not None
        else (json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None)
    )
    request = Request(base + route, data=body, method=method, headers=headers)
    try:
        with urlopen(request, timeout=5) as response:
            return response.status, dict(response.headers), response.read()
    except HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def test_real_http_auth_csp_and_content_limits(board_http):
    base, _ = board_http
    code, _, body = http(base, "GET", "/healthz", token=None)
    assert code == 200 and b"file-native Memory DB" in body
    assert http(base, "GET", "/api/threads", token=None)[0] == 401
    assert http(base, "POST", "/api/threads", {"title": "X"}, token=None)[0] == 401
    assert http(base, "GET", "/api/threads", token="invalid")[0] == 401
    code, headers, html = http(base, "GET", "/", token=None)
    assert code == 200 and b"Alice Board" in html
    assert "default-src 'none'" in headers["Content-Security-Policy"]
    assert "no-store" in headers["Cache-Control"]
    code, _, js = http(base, "GET", "/static/app.js", token=None)
    assert code == 200 and b"textContent" in js and b"localStorage.setItem(" not in js
    assert http(base, "POST", "/api/threads", raw=b'{"a":1,"a":2}')[0] == 400
    assert http(base, "POST", "/api/threads", raw=b'{"title":"bad"}')[0] == 400


def test_real_http_posts_tasks_replay_and_export(board_http):
    base, store = board_http
    status, _, result = http(
        base,
        "POST",
        "/api/threads",
        {
            "title": "Independent review",
            "body": "<script>alert(1)</script>",
            "category": "decision",
            "request_id": key("http-thread"),
        },
    )
    assert status == 201
    topic = json.loads(result)
    assert topic["id"] == "thread-000002"
    assert "<script>" in topic["posts"][0]["body"]
    status, _, repeated = http(
        base,
        "POST",
        "/api/threads",
        {
            "title": "Independent review",
            "body": "<script>alert(1)</script>",
            "category": "decision",
            "request_id": key("http-thread"),
        },
    )
    assert status == 201 and repeated == result
    assert (
        http(
            base,
            "POST",
            "/api/threads",
            {
                "title": "Different",
                "body": "No",
                "category": "decision",
                "request_id": key("http-thread"),
            },
        )[0]
        == 409
    )
    status, _, posted = http(
        base,
        "POST",
        "/api/threads/thread-000002/posts",
        {
            "body": "Fault evidence stored",
            "request_id": key("http-post"),
        },
    )
    assert status == 201 and b"Fault evidence" in posted
    assert http(base, "GET", "/api/threads/thread-000002")[0] == 200
    status, _, created = http(
        base,
        "POST",
        "/api/tasks",
        {
            "title": "Add owner acceptance",
            "depends_on": [],
            "request_id": key("http-task"),
        },
    )
    assert status == 201
    task = json.loads(created)
    assert (
        http(
            base,
            "POST",
            f"/api/tasks/{task['id']}/status",
            {
                "status": "done",
                "accepted_by_owner": True,
                "request_id": key("skip-review"),
            },
        )[0]
        == 409
    )
    status, _, exported = http(base, "GET", "/api/export")
    assert status == 200
    assert json.loads(exported)["sequence"] == store.export()["sequence"]
    assert TOKEN.encode() not in exported
    assert http(base, "GET", "/api/export", token=None)[0] == 401
    assert http(base, "GET", "/api/unknown")[0] == 404


def test_refuses_public_bind_and_weak_token(tmp_path):
    with BoardStore(tmp_path / "db.memory") as store:
        with pytest.raises(ValueError):
            create_server(store, TOKEN, host="0.0.0.0")
        with pytest.raises(ValueError):
            create_server(store, "short")


def test_seed_incident_is_durable_and_deduplicated(tmp_path):
    from project_board.seed import seed

    path = str(tmp_path / "seed.memory")
    first = seed(path)
    assert first == seed(path)
    with BoardStore(path) as db:
        assert db.export()["sequence"] == 2
        thread = db.thread(first["thread_id"])
        assert len(thread["posts"]) == 2
        assert "ChatGPT" in thread["posts"][1]["body"]


def test_real_fsync_fault_fences_writes_then_replay_is_idempotent(tmp_path, monkeypatch):
    import errno

    path = tmp_path / "fsync.memory"
    rid = key("injected-fsync")
    with BoardStore(path) as db:
        original = os.fsync
        monkeypatch.setattr(
            os,
            "fsync",
            lambda _fd: (_ for _ in ()).throw(OSError(errno.EIO, "injected fsync")),
        )
        with pytest.raises(BoardUnavailable, match="recovery required"):
            db.post("thread-000001", "maybe durable", rid)
        assert db.export()["sequence"] == 1
        with pytest.raises(BoardUnavailable, match="uncertain write"):
            db.post("thread-000001", "should be refused", key("second-fsync"))
        monkeypatch.setattr(os, "fsync", original)
    with BoardStore(path) as db:
        post = db.post("thread-000001", "maybe durable", rid)
        assert post["body"] == "maybe durable"
        assert len(db.thread("thread-000001")["posts"]) == 2
        assert db.export()["sequence"] == 2


def test_health_fails_closed_after_uncertain_commit(board_http, monkeypatch):
    base, store = board_http

    def fail(*_args):
        raise OSError("simulated fsync failure")

    monkeypatch.setattr(store._db, "put", fail)
    assert (
        http(
            base,
            "POST",
            "/api/threads/thread-000001/posts",
            {"body": "uncertain", "request_id": key("http-uncertain")},
        )[0]
        == 503
    )
    code, _, response = http(base, "GET", "/healthz", token=None)
    assert code == 503 and json.loads(response)["ok"] is False


def test_content_length_overflow_fails_with_400(board_http):
    import http.client
    from urllib.parse import urlsplit

    base, _ = board_http
    parsed = urlsplit(base)
    connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=5)
    try:
        connection.request(
            "POST",
            "/api/threads",
            b"{}",
            {
                "Authorization": "Bearer " + TOKEN,
                "Content-Type": "application/json",
                "Content-Length": "9" * 8000,
            },
        )
        response = connection.getresponse()
        assert response.status == 400
    finally:
        connection.close()
