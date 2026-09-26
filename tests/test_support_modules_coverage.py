import json
import runpy
import sqlite3
from datetime import datetime, timezone

import agent_context
import agent_runner
import archiver
import sdk
import send_logs
import yc_logging


def test_agent_context_builds_memory_prompt_and_limits_history(monkeypatch):
    monkeypatch.setattr(agent_context, "get_global_memory_summary", lambda: "GLOBAL")
    history = [{"role": "user", "text": f"message-{index}"} for index in range(12)]
    history[-2]["role"] = "assistant"
    history[-1]["role"] = "ai"
    monkeypatch.setattr(agent_context, "get_messages", lambda conversation_id: history)

    messages = agent_context.build_prompt_with_memory("conv-1", "SYSTEM")

    assert messages[0] == {"role": "system", "text": "SYSTEM\nGLOBAL"}
    assert len(messages) == 11
    assert messages[1]["text"] == "message-2"
    assert messages[-2]["role"] == "assistant"
    assert messages[-1]["role"] == "assistant"

    monkeypatch.setattr(agent_context, "get_messages", lambda conversation_id: [])
    default_prompt = agent_context.build_prompt_with_memory("conv-2")
    assert "Alice Pro" in default_prompt[0]["text"]


def test_agent_core_dispatches_and_normalizes_tool_arguments(monkeypatch):
    calls = []

    def fake_dispatch(name, arguments):
        calls.append((name, arguments))
        return {"name": name, "arguments": arguments}

    monkeypatch.setattr(agent_runner, "dispatch_tool", fake_dispatch)
    core = agent_runner.AgentCore("custom prompt")

    assert core.system_prompt == "custom prompt"
    assert core.tools is agent_runner.TOOLS_SCHEMA
    assert core.execute_tool_call("direct", {"value": 1})["name"] == "direct"

    results = core.handle_turn(
        [
            {
                "id": "call-1",
                "function": {"name": "json-tool", "arguments": '{"value": 2}'},
            },
            {
                "function": {"name": "broken-json", "arguments": "{"},
            },
            {
                "id": "call-3",
                "function": {"name": "dict-tool", "arguments": {"value": 3}},
            },
        ]
    )

    assert results[0]["tool_call_id"] == "call-1"
    assert results[0]["output"]["arguments"] == {"value": 2}
    assert results[1]["tool_call_id"] == "call_default"
    assert results[1]["output"]["arguments"] == {}
    assert results[2]["output"]["arguments"] == {"value": 3}
    assert calls[-3:] == [
        ("json-tool", {"value": 2}),
        ("broken-json", {}),
        ("dict-tool", {"value": 3}),
    ]


def _create_archive_database(path, rows=()):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE messages (id INTEGER, content TEXT, created_at TEXT)")
    conn.executemany("INSERT INTO messages VALUES (?, ?, ?)", rows)
    conn.commit()
    conn.close()


def test_archiver_handles_sqlite_rows_empty_database_and_mutex(monkeypatch, tmp_path):
    monkeypatch.setattr(archiver, "is_postgres_configured", lambda: False)
    db_path = tmp_path / "archive.sqlite"
    _create_archive_database(
        db_path,
        [
            (1, "old", "2000-01-01T00:00:00+00:00"),
            (2, "new", datetime.now(timezone.utc).isoformat()),
        ],
    )

    worker = archiver.DatabaseArchiver(str(db_path))
    assert worker.run_archive() is True
    conn = sqlite3.connect(db_path)
    assert conn.execute("SELECT content FROM messages ORDER BY id").fetchall() == [("new",)]
    conn.close()

    empty_path = tmp_path / "empty.sqlite"
    _create_archive_database(empty_path)
    assert archiver.DatabaseArchiver(str(empty_path)).run_archive() is True

    locked = archiver.DatabaseArchiver(str(empty_path))
    assert locked._mutex.acquire(blocking=False)
    try:
        assert locked.run_archive() is False
    finally:
        locked._mutex.release()


def test_archiver_uses_postgres_connection_and_rolls_back_errors(monkeypatch):
    class EmptyCursor:
        def execute(self, sql, params):
            self.last = (sql, params)

        def fetchall(self):
            return []

    class EmptyConnection:
        def __init__(self):
            self.closed = False

        def cursor(self):
            return EmptyCursor()

        def close(self):
            self.closed = True

    postgres_conn = EmptyConnection()
    monkeypatch.setattr(archiver, "is_postgres_configured", lambda: True)
    monkeypatch.setattr(archiver.database, "get_conn", lambda: postgres_conn)
    assert archiver.DatabaseArchiver("unused").run_archive() is True
    assert postgres_conn.closed is True

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
    monkeypatch.setattr(archiver, "is_postgres_configured", lambda: False)
    monkeypatch.setattr(archiver.sqlite3, "connect", lambda path: failing_conn)

    assert archiver.DatabaseArchiver("broken").run_archive() is False
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
