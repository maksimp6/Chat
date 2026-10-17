"""Regression tests for agent_runtime/cli.py after retiring the Alice class names (#1316).

The class names are internal, so they change. The wire model key and the ALICE_*
environment variables are compatibility contracts, so they must not change. The
error-path tests cover every changed line, because CI requires 100% diff coverage.
"""

import pytest
import requests

from agent_runtime import cli


class _Response:
    def __init__(self, status_code=200, payload=None, *, invalid_json=False):
        self.status_code = status_code
        self._payload = payload
        self._invalid_json = invalid_json

    def json(self):
        if self._invalid_json:
            raise ValueError("not json")
        return self._payload


class _Session:
    def __init__(self, response=None, error=None):
        self._response = response
        self._error = error

    def request(self, *args, **kwargs):
        if self._error is not None:
            raise self._error
        return self._response


class _FakeClient:
    def __init__(self, *, create_error=None, send_error=None):
        self._create_error = create_error
        self._send_error = send_error

    def create_conversation(self, *, title="Alice Pro CLI"):
        if self._create_error is not None:
            raise self._create_error
        return "conv-1"

    def send_message(self, conversation_id, message, *, session_id=None):
        if self._send_error is not None:
            raise self._send_error
        return {"reply": "hi"}


def _client(response=None, error=None):
    return cli.CliClient(
        base_url="http://backend.test",
        short_token="secret-token",
        session=_Session(response, error),
    )


def test_cli_classes_use_neutral_names():
    assert issubclass(cli.CliError, RuntimeError)
    assert isinstance(cli.CliClient, type)
    assert not hasattr(cli, "AliceCliError")
    assert not hasattr(cli, "AliceCliClient")


def test_default_model_key_is_unchanged_for_the_backend_contract():
    assert cli.DEFAULT_MODEL == "aliceai-llm"


def test_alice_environment_variables_still_configure_the_client(monkeypatch):
    monkeypatch.setenv("ALICE_BASE_URL", "http://env.test/")
    monkeypatch.setenv("ALICE_SHORT_TOKEN", "  env-token  ")
    monkeypatch.setenv("ALICE_MODEL", "env-model")

    client = cli.CliClient()

    assert client.base_url == "http://env.test"
    assert client.short_token == "env-token"
    assert client.model == "env-model"


def test_unreachable_backend_raises_cli_error_without_secret():
    client = _client(error=requests.ConnectionError("refused"))

    with pytest.raises(cli.CliError, match="backend is unavailable") as info:
        client.create_conversation()

    assert "secret-token" not in str(info.value)


def test_invalid_json_is_reported():
    client = _client(response=_Response(invalid_json=True))

    with pytest.raises(cli.CliError, match="returned invalid JSON"):
        client.create_conversation()


def test_http_error_reports_status_and_backend_message():
    client = _client(response=_Response(500, {"error": "boom"}))

    with pytest.raises(cli.CliError, match="Alice Pro HTTP 500: boom"):
        client.create_conversation()


def test_non_object_json_is_rejected():
    client = _client(response=_Response(200, ["not", "an", "object"]))

    with pytest.raises(cli.CliError, match="returned an invalid response"):
        client.create_conversation()


def test_missing_conversation_id_is_rejected():
    client = _client(response=_Response(200, {}))

    with pytest.raises(cli.CliError, match="did not return a conversation id"):
        client.create_conversation()


def test_main_reports_startup_failure(monkeypatch, capsys):
    monkeypatch.delenv("ALICE_CONVERSATION_ID", raising=False)
    monkeypatch.setattr(cli, "CliClient", lambda: _FakeClient(create_error=cli.CliError("down")))

    cli.main()

    assert "Alice Pro CLI startup failed: down" in capsys.readouterr().out


def test_main_reports_request_failure_and_exits_on_exit_command(monkeypatch, capsys):
    monkeypatch.setenv("ALICE_CONVERSATION_ID", "conv-1")
    monkeypatch.setattr(cli, "CliClient", lambda: _FakeClient(send_error=cli.CliError("nope")))
    answers = iter(["hello", "exit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

    cli.main()

    assert "Alice Pro request failed: nope" in capsys.readouterr().out
