import json
import logging
import runpy
from types import SimpleNamespace

import pytest
from flask import Flask

import cli_agent
import logger as alice_logging


class FakeLog:
    def __init__(self):
        self.calls = []

    def info(self, message, **kwargs):
        self.calls.append(("info", message, kwargs))

    def debug(self, message, **kwargs):
        self.calls.append(("debug", message, kwargs))

    def error(self, message, **kwargs):
        self.calls.append(("error", message, kwargs))

    def warning(self, message, **kwargs):
        self.calls.append(("warning", message, kwargs))


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload
        self.raised = False

    def raise_for_status(self):
        self.raised = True

    def json(self):
        return self.payload


def test_cli_query_llm_builds_yandex_request(monkeypatch):
    captured = {}
    response = FakeResponse({"result": {"alternatives": [{"message": {"text": '{"text":"ok"}'}}]}})

    def fake_post(url, headers, json, timeout):
        captured.update(url=url, headers=headers, json=json, timeout=timeout)
        return response

    monkeypatch.setenv("YANDEX_PROJECT_ID", "project-1")
    monkeypatch.setenv("YANDEX_API_KEY", "secret")
    monkeypatch.setattr(cli_agent.requests, "post", fake_post)

    result = cli_agent.query_llm([{"role": "user", "text": "hello"}])

    assert result == '{"text":"ok"}'
    assert response.raised is True
    assert captured["url"] == cli_agent.API_URL
    assert captured["headers"]["Authorization"] == "Api-Key secret"
    assert captured["json"]["modelUri"] == "gpt://project-1/yandexgpt/latest"
    assert captured["timeout"] == 60


@pytest.mark.parametrize(
    ("stdout", "stderr", "returncode", "expected"),
    [
        ("hello\n", "", 0, "STDOUT:\nhello\n"),
        ("", "boom\n", 2, "STDERR:\nboom\n"),
        ("", "", 0, "(нет вывода)\n"),
    ],
)
def test_cli_run_shell_formats_process_result(
    monkeypatch, capsys, stdout, stderr, returncode, expected
):
    monkeypatch.setattr(
        cli_agent.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            stdout=stdout,
            stderr=stderr,
            returncode=returncode,
        ),
    )

    result = cli_agent.run_shell("echo test")

    assert result.startswith(f"EXIT_CODE: {returncode}\n")
    assert expected in result
    assert "Выполняю: echo test" in capsys.readouterr().out


def test_cli_parse_action_supports_plain_and_fenced_json():
    assert cli_agent.parse_action('{"text":"plain"}') == {"text": "plain"}
    assert cli_agent.parse_action('```json\n{"command":"pwd"}\n```') == {"command": "pwd"}
    with pytest.raises(json.JSONDecodeError):
        cli_agent.parse_action("not-json")


def test_cli_main_runs_command_then_text(monkeypatch, capsys):
    inputs = iter(["status", "exit"])
    replies = iter(
        [
            '{"command":"pwd","explanation":"check directory"}',
            '{"text":"done"}',
        ]
    )
    histories = []

    monkeypatch.setattr("builtins.input", lambda prompt: next(inputs))
    monkeypatch.setattr(
        cli_agent,
        "query_llm",
        lambda history: histories.append(list(history)) or next(replies),
    )
    monkeypatch.setattr(cli_agent, "run_shell", lambda command: "EXIT_CODE: 0\nSTDOUT:\n/tmp\n")

    cli_agent.main()

    output = capsys.readouterr().out
    assert "CLI Agent активен" in output
    assert "check directory" in output
    assert "EXIT_CODE: 0" in output
    assert "AI > done" in output
    assert histories[0][-1] == {"role": "user", "text": "status"}
    assert histories[1][-1]["text"].startswith("Вывод команды:")


def test_cli_main_handles_invalid_reply_and_request_failure(monkeypatch, capsys):
    inputs = iter(["first", "second", "exit"])
    replies = iter(["not-json", RuntimeError("offline")])

    def fake_query(history):
        value = next(replies)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr("builtins.input", lambda prompt: next(inputs))
    monkeypatch.setattr(cli_agent, "query_llm", fake_query)

    cli_agent.main()

    output = capsys.readouterr().out
    assert "AI > not-json" in output
    assert "AI request failed: offline" in output


def test_cli_main_handles_eof_and_module_entrypoint(monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda prompt: (_ for _ in ()).throw(EOFError()))
    cli_agent.main()
    assert "CLI Agent активен" in capsys.readouterr().out

    monkeypatch.setattr("builtins.input", lambda prompt: "exit")
    runpy.run_path(cli_agent.__file__, run_name="__main__")
    assert "CLI Agent активен" in capsys.readouterr().out


def test_force_critical_filter_and_yc_handler(monkeypatch):
    record = logging.LogRecord("source", logging.INFO, __file__, 1, "hello", (), None)
    critical_filter = alice_logging.ForceCriticalFilter()
    assert critical_filter.filter(record) is True
    assert record.levelno == logging.CRITICAL
    assert record.levelname == "CRITICAL"

    emitted = []
    monkeypatch.setattr(
        alice_logging.yc_logger,
        "emit",
        lambda level, message, stream_name: emitted.append((level, message, stream_name)),
    )
    handler = alice_logging.YCLoggingHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler.emit(record)
    assert emitted == [("CRITICAL", "hello", "source")]

    monkeypatch.setattr(
        alice_logging.yc_logger,
        "emit",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("log failure")),
    )
    handler.emit(record)


def test_alice_logger_builds_handlers_and_falls_back_to_app(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "logs").mkdir()
    monkeypatch.setattr(alice_logging.yc_logger, "emit", lambda *args, **kwargs: None)

    instance = alice_logging.AliceLogger()

    assert set(instance.loggers) == {
        "app",
        "api",
        "voice",
        "chat",
        "db",
        "export",
        "search",
        "prompts",
        "stats",
        "error",
    }
    assert instance.get("missing") is instance.get("app")
    instance.get("api").info("hello")
    assert (tmp_path / "logs" / "api_debug.txt").exists()


def test_log_function_records_success_and_failure(monkeypatch):
    fake = FakeLog()
    monkeypatch.setattr(alice_logging.alice_logger, "get", lambda name: fake)

    @alice_logging.log_function("api")
    def add(a, b):
        return a + b

    @alice_logging.log_function("api")
    def fail():
        raise ValueError("broken")

    assert add(2, 3) == 5
    with pytest.raises(ValueError):
        fail()

    assert any("вызвана" in call[1] for call in fake.calls)
    assert any("завершена успешно" in call[1] for call in fake.calls)
    assert any(call[0] == "error" and "broken" in call[1] for call in fake.calls)


def test_log_request_records_json_and_errors(monkeypatch):
    fake = FakeLog()
    monkeypatch.setattr(alice_logging.alice_logger, "get", lambda name: fake)
    app = Flask(__name__)

    @alice_logging.log_request("api")
    def endpoint():
        return "ok"

    @alice_logging.log_request("api")
    def broken():
        raise RuntimeError("request failed")

    with app.test_request_context("/demo", method="POST", json={"z": 1, "a": 2}):
        assert endpoint() == "ok"
    with app.test_request_context("/broken", method="GET"):
        with pytest.raises(RuntimeError):
            broken()

    assert any("POST /demo" in call[1] for call in fake.calls)
    assert any("fields=a,z" in call[1] for call in fake.calls)
    assert any(call[0] == "error" and "request failed" in call[1] for call in fake.calls)


def test_logging_helpers_route_to_named_loggers(monkeypatch):
    logs = {}

    def get_logger(name):
        return logs.setdefault(name, FakeLog())

    monkeypatch.setattr(alice_logging.alice_logger, "get", get_logger)

    alice_logging.log_error("TYPE", "context")
    alice_logging.log_error("TYPE")
    alice_logging.log_voice("voice")
    alice_logging.log_chat("chat", "warning")
    alice_logging.log_db("db")
    alice_logging.log_search("search")
    alice_logging.log_prompt("prompt")
    alice_logging.log_export("export")
    alice_logging.log_stats("stats")

    assert "[TYPE] context" in logs["error"].calls[0][1]
    assert logs["error"].calls[1][1] == "[TYPE]"
    assert logs["voice"].calls[0][1] == "[VOICE] voice"
    assert logs["chat"].calls[0][0] == "warning"
    assert logs["db"].calls[0][1] == "[DB] db"
    assert logs["search"].calls[0][1] == "[SEARCH] search"
    assert logs["prompts"].calls[0][1] == "[PROMPT] prompt"
    assert logs["export"].calls[0][1] == "[EXPORT] export"
    assert logs["stats"].calls[0][1] == "[STATS] stats"
