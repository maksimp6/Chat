import json
import runpy
from datetime import datetime, timezone

import archiver
import db
import sdk
import send_logs
import yc_logging


def _add_archive_message(conv_id, content, created_at):
    conn = db.get_conn()
    conn.execute(
        "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
        (conv_id, "user", content, created_at),
    )
    conn.commit()
    conn.close()


def test_archiver_deletes_old_messages_on_the_application_database(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "archive.sqlite"))
    db.init_db()
    db.create_conversation("archive-conv", "Archive", "model")
    now = int(datetime.now(timezone.utc).timestamp())
    _add_archive_message("archive-conv", "old", now - 31 * 24 * 3600)
    _add_archive_message("archive-conv", "new", now)

    worker = archiver.DatabaseArchiver()
    assert worker.run_archive() is True
    assert [m["text"] for m in db.get_messages("archive-conv")] == ["new"]

    # Nothing left to archive is still a successful run.
    assert archiver.DatabaseArchiver().run_archive() is True

    locked = archiver.DatabaseArchiver()
    assert locked._mutex.acquire(blocking=False)
    try:
        assert locked.run_archive() is False
    finally:
        locked._mutex.release()


def test_archiver_rolls_back_errors(monkeypatch):
    class FailingCursor:
        def execute(self, sql, params):
            raise RuntimeError("database failure")

    class FailingConnection:
        def __init__(self):
            self.rolled_back = False
            self.closed = False

        def cursor(self):
            return FailingCursor()

        def rollback(self):
            self.rolled_back = True

        def close(self):
            self.closed = True

    failing_conn = FailingConnection()
    monkeypatch.setattr(archiver.database, "get_conn", lambda: failing_conn)

    assert archiver.DatabaseArchiver().run_archive() is False
    assert failing_conn.rolled_back is True
    assert failing_conn.closed is True


class _FakeResponse:
    def __init__(self, payload, status_code=200, text="ok"):
        self.payload = payload
        self.status_code = status_code
        self.text = text
        self.raised = False

    def raise_for_status(self):
        self.raised = True

    def json(self):
        return self.payload


class _FakeSession:
    def __init__(self):
        self.headers = {}
        self.calls = []

    def post(self, url, json):
        self.calls.append(("POST", url, json))
        if url.endswith("/conversations"):
            return _FakeResponse({"id": "conv-1"})
        return _FakeResponse({"response": "ok"})

    def get(self, url):
        self.calls.append(("GET", url, None))
        return _FakeResponse({"models": []})


def test_sdk_builds_headers_and_yandex_requests(monkeypatch):
    session = _FakeSession()
    monkeypatch.setattr(sdk.requests, "Session", lambda: session)

    client = sdk.AliceSDK("key", "project", "https://example.test/v1/")
    assert client.base_url == "https://example.test/v1"
    assert session.headers["Authorization"] == "Api-Key key"
    assert session.headers["x-yc-project-id"] == "project"

    assert client.create_conversation() == {"id": "conv-1"}
    assert client.send_message("conv-1", "hello", model="model-a") == {"response": "ok"}
    assert client.list_models() == {"models": []}

    response_call = next(call for call in session.calls if call[1].endswith("/responses"))
    assert response_call[2]["model"] == "gpt://project/model-a/latest"
    assert response_call[2]["conversation"] == "conv-1"
    assert response_call[2]["input"] == [{"role": "user", "content": "hello"}]


def test_sdk_main_example_runs_with_fake_http(monkeypatch, capsys):
    session = _FakeSession()
    monkeypatch.setattr(sdk.requests, "Session", lambda: session)
    monkeypatch.setenv("YANDEX_API_KEY", "key")
    monkeypatch.setenv("YANDEX_PROJECT_ID", "project")

    runpy.run_path(sdk.__file__, run_name="__main__")

    output = capsys.readouterr().out
    assert "Создан диалог: conv-1" in output
    assert "Ответ:" in output


def test_send_logs_collects_files_and_handles_transport(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    assert send_logs.collect_logs() == []
    assert "not found" in capsys.readouterr().out

    log_dir = tmp_path / "execution_logs"
    log_dir.mkdir()
    (log_dir / "good.json").write_text('{"ok": true}', encoding="utf-8")
    (log_dir / "bad.json").write_text("{bad", encoding="utf-8")
    (log_dir / "ignore.txt").write_text("ignored", encoding="utf-8")

    assert send_logs.collect_logs() == [{"ok": True}]
    assert "Error reading bad.json" in capsys.readouterr().out

    monkeypatch.setattr(send_logs, "collect_logs", lambda: [])
    send_logs.send_logs()
    assert "No logs to send" in capsys.readouterr().out

    captured = {}

    def fake_post(url, json, headers, timeout):
        captured.update(url=url, json=json, headers=headers, timeout=timeout)
        return _FakeResponse({}, status_code=202, text="accepted")

    monkeypatch.setattr(send_logs, "collect_logs", lambda: [{"event": "x"}])
    monkeypatch.setattr(send_logs.requests, "post", fake_post)
    send_logs.send_logs()
    assert captured["timeout"] == 30
    assert captured["json"]["logs"] == [{"event": "x"}]
    assert "Status: 202" in capsys.readouterr().out

    def failing_post(*args, **kwargs):
        raise send_logs.requests.exceptions.RequestException("offline")

    monkeypatch.setattr(send_logs.requests, "post", failing_post)
    send_logs.send_logs()
    assert "Error: offline" in capsys.readouterr().out


def test_send_logs_main_entrypoint(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(send_logs.requests, "post", lambda *args, **kwargs: _FakeResponse({}))
    runpy.run_path(send_logs.__file__, run_name="__main__")
    assert "No logs to send" in capsys.readouterr().out


def test_local_logger_writes_jsonl_handles_write_errors_and_flushes(monkeypatch, tmp_path, capsys):
    path = tmp_path / "app.jsonl"
    logger = yc_logging.LocalLogger(str(path))
    logger.emit("info", 123, "test-stream")

    entry = json.loads(path.read_text(encoding="utf-8"))
    assert entry["level"] == "INFO"
    assert entry["stream"] == "test-stream"
    assert entry["message"] == "123"
    assert "[INFO] 123" in capsys.readouterr().out

    import builtins

    real_open = builtins.open
    monkeypatch.setattr(
        builtins, "open", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("x"))
    )
    logger.emit("error", "cannot-write")
    monkeypatch.setattr(builtins, "open", real_open)
    assert "[ERROR] cannot-write" in capsys.readouterr().out

    logger.flush()
