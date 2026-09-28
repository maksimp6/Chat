import logging
import runpy

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
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, json, timeout):
        self.calls.append(
            {
                "method": method,
                "url": url,
                "json": json,
                "timeout": timeout,
            }
        )
        return self.responses.pop(0)


def test_cli_client_routes_conversation_and_chat_through_alice_backend():
    session = FakeSession(
        [
            FakeResponse({"id": "conv-1"}, status_code=201),
            FakeResponse({"reply": "done"}),
        ]
    )
    client = cli_agent.AliceCliClient(
        base_url="https://alice.example",
        short_token="short-token",
        model="aliceai-llm",
        timeout=12,
        session=session,
    )

    conversation_id = client.create_conversation(title="CLI test")
    response = client.send_message(conversation_id, "hello", session_id="session-1")

    assert conversation_id == "conv-1"
    assert response["reply"] == "done"
    assert session.calls[0]["url"] == "https://alice.example/short-token/api/conversations"
    assert session.calls[0]["json"] == {"title": "CLI test", "model": "aliceai-llm"}
    assert session.calls[1]["url"] == "https://alice.example/short-token/api/chat"
    assert session.calls[1]["json"] == {
        "conversation_id": "conv-1",
        "session_id": "session-1",
        "message": "hello",
        "model": "aliceai-llm",
    }
    assert session.calls[1]["timeout"] == 12


def test_cli_client_uses_standard_approval_endpoint():
    session = FakeSession([FakeResponse({"reply": "approved"})])
    client = cli_agent.AliceCliClient(
        base_url="http://127.0.0.1:5000",
        short_token="",
        model="aliceai-llm",
        session=session,
    )

    result = client.execute_approved(
        "conv-1",
        {"name": "git_push", "arguments": {"remote": "origin"}},
    )

    assert result == {"reply": "approved"}
    assert session.calls[0]["url"] == "http://127.0.0.1:5000/api/mcp/execute-approved"
    assert session.calls[0]["json"] == {
        "conversation_id": "conv-1",
        "name": "git_push",
        "arguments": {"remote": "origin"},
        "model": "aliceai-llm",
    }


def test_cli_backend_error_does_not_disclose_short_token():
    client = cli_agent.AliceCliClient(
        base_url="https://alice.example",
        short_token="do-not-leak",
        session=FakeSession([FakeResponse({"error": "authentication required"}, status_code=401)]),
    )

    with pytest.raises(cli_agent.AliceCliError) as exc:
        client.create_conversation()

    assert "HTTP 401" in str(exc.value)
    assert "do-not-leak" not in str(exc.value)


def test_cli_main_handles_normal_reply_and_approval(monkeypatch, capsys):
    class FakeClient:
        def __init__(self):
            self.messages = []
            self.approved = []

        def create_conversation(self, title="Alice Pro CLI"):
            return "conv-1"

        def send_message(self, conversation_id, message, session_id=None):
            self.messages.append((conversation_id, message, session_id))
            if message == "first":
                return {"reply": "hello"}
            return {
                "requires_approval": True,
                "tool_call": {
                    "name": "git_push",
                    "description": "Push changes",
                    "arguments": {"remote": "origin"},
                },
            }

        def execute_approved(self, conversation_id, tool_call):
            self.approved.append((conversation_id, tool_call))
            return {"reply": "push complete"}

    fake = FakeClient()
    inputs = iter(["first", "second", "y", "exit"])
    monkeypatch.setattr(cli_agent, "AliceCliClient", lambda: fake)
    monkeypatch.setattr("builtins.input", lambda prompt: next(inputs))

    cli_agent.main()

    output = capsys.readouterr().out
    assert "каноническому runtime" in output
    assert "AI > hello" in output
    assert "Требуется подтверждение" in output
    assert "AI > push complete" in output
    assert fake.messages == [
        ("conv-1", "first", "conv-1"),
        ("conv-1", "second", "conv-1"),
    ]
    assert fake.approved[0][0] == "conv-1"
    assert fake.approved[0][1]["name"] == "git_push"


def test_cli_main_handles_backend_failure_and_module_entrypoint(monkeypatch, capsys):
    class FakeClient:
        def create_conversation(self, title="Alice Pro CLI"):
            return "conv-1"

        def send_message(self, conversation_id, message, session_id=None):
            raise cli_agent.AliceCliError("backend unavailable")

    inputs = iter(["hello", "exit"])
    monkeypatch.setattr(cli_agent, "AliceCliClient", lambda: FakeClient())
    monkeypatch.setattr("builtins.input", lambda prompt: next(inputs))

    cli_agent.main()

    output = capsys.readouterr().out
    assert "Alice Pro request failed: backend unavailable" in output

    monkeypatch.setenv("ALICE_CONVERSATION_ID", "conv-existing")
    monkeypatch.setattr("builtins.input", lambda prompt: "exit")
    runpy.run_path(cli_agent.__file__, run_name="__main__")
    assert "Conversation: conv-existing" in capsys.readouterr().out



def test_cli_client_handles_transport_invalid_json_and_invalid_payload():
    class FailingSession:
        def request(self, *args, **kwargs):
            raise cli_agent.requests.ConnectionError("offline secret-url")

    client = cli_agent.AliceCliClient(session=FailingSession())
    with pytest.raises(cli_agent.AliceCliError, match="backend is unavailable"):
        client.create_conversation()

    class InvalidJsonResponse:
        status_code = 200

        def json(self):
            raise ValueError("bad json")

    client = cli_agent.AliceCliClient(session=FakeSession([InvalidJsonResponse()]))
    with pytest.raises(cli_agent.AliceCliError, match="invalid JSON"):
        client.create_conversation()

    client = cli_agent.AliceCliClient(session=FakeSession([FakeResponse(["not", "object"])]))
    with pytest.raises(cli_agent.AliceCliError, match="invalid response"):
        client.create_conversation()

    client = cli_agent.AliceCliClient(session=FakeSession([FakeResponse({})]))
    with pytest.raises(cli_agent.AliceCliError, match="conversation id"):
        client.create_conversation()


def test_cli_prints_backend_error_payload(capsys):
    cli_agent._print_reply({"error": "provider_unavailable"})
    assert "AI error > provider_unavailable" in capsys.readouterr().out


def test_cli_approval_can_be_declined_or_cancelled(monkeypatch, capsys):
    class NeverApprove:
        def execute_approved(self, *args, **kwargs):
            raise AssertionError("approval must not execute")

    payload = {
        "tool_call": {
            "name": "dangerous_tool",
            "description": "Dangerous tool",
            "arguments": {},
        }
    }

    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    cli_agent._handle_approval(NeverApprove(), "conv-1", payload)
    assert "Действие не выполнено" in capsys.readouterr().out

    monkeypatch.setattr(
        "builtins.input",
        lambda prompt: (_ for _ in ()).throw(EOFError()),
    )
    cli_agent._handle_approval(NeverApprove(), "conv-1", payload)
    assert "Действие не выполнено" in capsys.readouterr().out


def test_cli_startup_failure_is_reported(monkeypatch, capsys):
    class BrokenClient:
        def create_conversation(self, title="Alice Pro CLI"):
            raise cli_agent.AliceCliError("cannot create conversation")

    monkeypatch.delenv("ALICE_CONVERSATION_ID", raising=False)
    monkeypatch.setattr(cli_agent, "AliceCliClient", lambda: BrokenClient())

    cli_agent.main()

    assert "CLI startup failed: cannot create conversation" in capsys.readouterr().out

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
