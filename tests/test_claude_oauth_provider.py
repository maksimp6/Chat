"""Offline contract/integration tests. No OAuth login or model requests occur."""

import copy
import json
import subprocess
from types import SimpleNamespace

import pytest
import requests
from flask import Flask

from agent_runtime.logos_launch_hold import NativeLogosLaunchBlocked
from core.config import calculate_cost, calculate_full_cost
from economy.billing import aggregate_billing
from observability.trace_manager import ExecutionTrace
from provider_accounts import claude_oauth as provider
from tool_providers.registry import ToolRegistry

MODEL = "claude-opus-5-5"
SECRET = "synthetic-console-oauth-secret"


def envelope(*blocks, stop="end_turn"):
    return {
        "id": "msg-offline",
        "type": "message",
        "role": "assistant",
        "model": MODEL,
        "stop_reason": stop,
        "content": list(blocks) or [{"type": "text", "text": "Готово"}],
        "usage": {"input_tokens": 8, "output_tokens": 3},
    }


@pytest.fixture
def offline(monkeypatch):
    # This is a fake transport contract. The real hold has a separate negative control below.
    monkeypatch.setattr(provider, "enforce_native_launch_hold", lambda: None)
    monkeypatch.setattr(provider, "resolve_access_token", lambda: SECRET)
    monkeypatch.setattr(provider, "get_messages", lambda conv: [])
    state = SimpleNamespace(sent=[], replies=[envelope()], status=200, raw=None, failure=None)

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        @property
        def status_code(self):
            return state.status

        def iter_content(self, size):
            yield state.raw if state.raw is not None else json.dumps(state.replies.pop(0)).encode()

    class Session:
        def __init__(self):
            self.headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, **kwargs):
            state.sent.append((url, copy.deepcopy(kwargs)))
            if state.failure:
                raise state.failure
            return Response()

    monkeypatch.setattr(provider.requests, "Session", Session)
    return state


def test_production_hold_precedes_credentials_registry_and_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Production hold reached a credential, registry or network boundary")

    monkeypatch.setattr(provider, "resolve_access_token", forbidden)
    monkeypatch.setattr(provider.registry, "get_available_categories", forbidden)
    monkeypatch.setattr(provider.requests, "Session", forbidden)
    client = provider.ClaudeOAuthTransport()
    for ask in (client.ask, client.ask_with_mcp):
        with pytest.raises(NativeLogosLaunchBlocked):
            ask("test", MODEL)


def test_console_cli_profile_refresh_and_secret_capture(monkeypatch, caplog, capsys):
    calls = []
    monkeypatch.setattr(provider.shutil, "which", lambda name: "/usr/bin/ant")
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"):
        monkeypatch.setenv(name, "do-not-use")

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        value = json.dumps({"authentication": {"type": "user_oauth"}}) if "get" in argv else SECRET
        return SimpleNamespace(returncode=0, stdout=value, stderr="")

    monkeypatch.setattr(provider.subprocess, "run", run)
    assert provider.resolve_access_token() == SECRET
    assert calls[0][0] == ["/usr/bin/ant", "--profile", "logos", "profile", "get"]
    assert calls[1][0][-3:] == ["auth", "print-credentials", "--access-token"]
    for argv, kwargs in calls:
        assert SECRET not in repr(argv)
        assert kwargs["timeout"] == 20 and kwargs["stdin"] == subprocess.DEVNULL
        assert "ANTHROPIC_API_KEY" not in kwargs["env"]
        assert "ANTHROPIC_AUTH_TOKEN" not in kwargs["env"]
    assert SECRET not in caplog.text + capsys.readouterr().out


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"authentication": {"type": "api_key"}},
        {"authentication": {"type": "user_oauth"}, "base_url": "https://untrusted.test"},
        {"authentication": {"type": "user_oauth"}, "console_url": "https://untrusted.test"},
    ],
)
def test_non_console_profile_cannot_resolve_token(monkeypatch, config):
    monkeypatch.setattr(provider.shutil, "which", lambda name: "/usr/bin/ant")
    monkeypatch.setattr(provider, "_cli_json", lambda *args: config)
    monkeypatch.setattr(provider, "_cli_output", lambda *args: pytest.fail("must not read token"))
    with pytest.raises(provider.ClaudeOAuthError, match="first-party"):
        provider.resolve_access_token()


@pytest.mark.parametrize(
    "result",
    [
        SimpleNamespace(returncode=1, stdout=SECRET, stderr=SECRET),
        SimpleNamespace(returncode=0, stdout=SECRET, stderr="refresh failed " + SECRET),
        SimpleNamespace(returncode=0, stdout="x" * 65537, stderr=""),
    ],
)
def test_failed_refresh_is_sanitized(monkeypatch, result):
    monkeypatch.setattr(provider.subprocess, "run", lambda *args, **kwargs: result)
    with pytest.raises(provider.ClaudeOAuthError) as error:
        provider._cli_output("ant", [], {})
    assert SECRET not in str(error.value)


def test_missing_cli_and_invalid_profile_fail_locally(monkeypatch):
    monkeypatch.setattr(provider.shutil, "which", lambda name: None)
    with pytest.raises(provider.ClaudeOAuthError, match="ant CLI"):
        provider.resolve_access_token()
    monkeypatch.setattr(provider.shutil, "which", lambda name: "ant")
    monkeypatch.setenv("LOGOS_CLAUDE_OAUTH_PROFILE", "../../other")
    with pytest.raises(provider.ClaudeOAuthError, match="profile"):
        provider.resolve_access_token()


def test_request_history_usage_trace_unknown_price_and_redaction(offline, monkeypatch):
    monkeypatch.setattr(
        provider,
        "get_messages",
        lambda conv: [
            {"role": "user", "text": "раньше"},
            {"role": "assistant", "text": "ответ"},
            {"role": "user", "text": "сейчас"},
        ],
    )
    trace = ExecutionTrace()
    result = provider.ClaudeOAuthTransport().ask(
        "сейчас",
        MODEL,
        "conv",
        {"max_output_tokens": 12000, "instructions": "Ты Логос", "temperature": 0.5},
        trace,
    )
    url, request = offline.sent[0]
    assert url == provider.ENDPOINT
    assert request["headers"]["Authorization"] == "Bearer " + SECRET
    assert request["headers"]["anthropic-beta"] == "oauth-2025-04-20"
    assert request["allow_redirects"] is False and request["stream"] is True
    assert request["timeout"] == (10, 90)
    assert [item["content"] for item in request["json"]["messages"]] == [
        "раньше",
        "ответ",
        "сейчас",
    ]
    assert request["json"]["system"] == "Ты Логос"
    assert request["json"]["max_tokens"] == 12000
    assert result["status"] == "completed" and result["usage"]["total_tokens"] == 11
    finished = trace.finalize()
    assert SECRET not in json.dumps(finished)
    assert (
        finished["api_requests"][0]["correlation_id"] == finished["responses"][0]["correlation_id"]
    )
    ai = next(item for item in finished["billing"]["items"] if item["type"] == "ai")
    assert ai["provider"] == "anthropic" and ai["currency"] == "USD"
    assert ai["cost_status"] == "unknown" and ai["total_cost"] is None
    billing = aggregate_billing([ai])
    assert billing["provider"] == "anthropic" and billing["total_cost"] is None
    assert calculate_cost(MODEL, 8, 3) is None
    assert calculate_full_cost(MODEL, result["usage"]) is None


@pytest.mark.parametrize("status", [302, 401, 403, 429, 500])
def test_http_failure_has_no_retry_fallback_or_body(offline, status):
    offline.status, offline.raw = status, SECRET.encode()
    with pytest.raises(provider.ClaudeOAuthError, match=str(status)) as error:
        provider.ClaudeOAuthTransport().ask("test", MODEL)
    assert len(offline.sent) == 1 and SECRET not in str(error.value)


@pytest.mark.parametrize("raw", [b"not json", b"[]", b"x" * (provider.MAX_BYTES + 1)])
def test_malformed_and_oversized_responses_fail(offline, raw):
    offline.raw = raw
    with pytest.raises(provider.ClaudeOAuthError):
        provider.ClaudeOAuthTransport().ask("test", MODEL)
    assert len(offline.sent) == 1


def test_request_bound_and_connection_failure(offline):
    with pytest.raises(provider.ClaudeOAuthError, match="size limit"):
        provider.ClaudeOAuthTransport().ask("x" * (provider.MAX_BYTES + 1), MODEL)
    assert not offline.sent
    offline.failure = requests.Timeout(SECRET)
    with pytest.raises(provider.ClaudeOAuthError) as error:
        provider.ClaudeOAuthTransport().ask("test", MODEL)
    assert SECRET not in str(error.value) and len(offline.sent) == 1


@pytest.mark.parametrize(
    "params",
    [
        {"max_output_tokens": True},
        {"max_output_tokens": 0},
        {"max_output_tokens": 128001},
        {"stream": True},
        {"previous_response_id": "old"},
        {"reasoning": {"effort": "high"}},
        {"temperature": 2},
        {"temperature": 0.5, "top_p": 1},
        {"tools": [{"type": "web_search"}]},
        {"tools_config": {"file_search": {"enabled": True}}},
        {"input": [{"type": "function_call_output", "call_id": "foreign", "output": "ok"}]},
    ],
)
def test_unsupported_and_invalid_requests_fail_before_network(offline, params):
    with pytest.raises(provider.ClaudeOAuthError):
        provider.ClaudeOAuthTransport().ask("test", MODEL, params=params)
    assert not offline.sent


def test_incomplete_response_never_claims_completion(offline):
    offline.replies = [envelope(stop="max_tokens")]
    result = provider.ClaudeOAuthTransport().ask("test", MODEL)
    assert result["status"] == "incomplete"
    assert result["incomplete_details"] == {"reason": "max_tokens"}


@pytest.mark.parametrize(
    "change",
    [
        {"usage": {"input_tokens": -1, "output_tokens": 1}},
        {"usage": {"input_tokens": True, "output_tokens": 1}},
        {"usage": {"input_tokens": 1, "output_tokens": 1, "cache_read_input_tokens": "secret"}},
        {"content": [{"type": "tool_use", "id": "id", "name": "tool", "input": {}}]},
        {"stop_reason": "tool_use"},
        {"type": "error"},
        {"content": []},
    ],
)
def test_untrusted_response_is_rejected(change):
    data = envelope()
    data.update(change)
    with pytest.raises(provider.ClaudeOAuthError):
        provider.normalize_response(data)


@pytest.fixture
def tool_registry(monkeypatch):
    import chat.mcp_routes as routes
    import yandex_client_modules.mcp_mixin as mixin

    registry = object.__new__(ToolRegistry)
    registry._tools, registry._categories = {}, {}
    executed = []

    def handler(name):
        def execute(arguments):
            executed.append(name)
            return {"value": "ok"}

        return execute

    for name, read_only in (("inspect", True), ("change", False)):
        registry.register(
            "fixture",
            name,
            {
                "description": name,
                "read_only": read_only,
                "requires_approval": not read_only,
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
                "func": handler(name),
            },
        )
    for module in (provider, routes, mixin):
        monkeypatch.setattr(module, "registry", registry)
    monkeypatch.setattr(routes.mcp_storage, "list_servers", list)
    return executed


@pytest.mark.parametrize("name,expected", [("inspect", ["inspect"]), ("change", [])])
def test_logos_tool_execution_and_approval_use_real_universal_boundary(
    offline, tool_registry, name, expected
):
    from chat.mcp_routes import LogosClient

    call = {"type": "tool_use", "id": "toolu1", "name": name, "input": {}}
    offline.replies = [envelope(call, stop="tool_use"), envelope()]
    client = object.__new__(LogosClient)
    trace = ExecutionTrace()
    result = client.ask_with_mcp(
        "test", MODEL, "conv", {"active_tool_categories": ["fixture"]}, trace
    )
    assert tool_registry == expected
    if name == "change":
        assert result["output"][0]["type"] == "function_call"
        assert len(offline.sent) == 1
    else:
        assert result["status"] == "completed" and len(offline.sent) == 2
        messages = offline.sent[1][1]["json"]["messages"]
        assert messages[-2]["content"] == [call]
        assert messages[-1]["content"][0]["tool_use_id"] == "toolu1"
        assert "ok" in messages[-1]["content"][0]["content"]
        assert len(trace.trace["tool_calls"]) == 1


def test_local_conversation_creation_does_not_need_yandex_or_oauth(monkeypatch):
    import chat.mcp_routes as routes

    monkeypatch.setattr(routes, "get_current_owner_id", lambda required=False: None)
    monkeypatch.setattr(
        routes.LogosClient, "create_conversation", lambda *a, **kw: pytest.fail("Yandex used")
    )
    monkeypatch.setattr(provider, "resolve_access_token", lambda: pytest.fail("OAuth used"))
    monkeypatch.setattr(routes, "create_conversation", lambda *args: None)
    app = Flask(__name__)
    app.register_blueprint(routes.mcp_bp)
    response = app.test_client().post("/api/conversations", json={"model": MODEL})
    assert response.status_code == 201
    assert response.get_json()["id"]


def test_yandex_routing_is_preserved(monkeypatch):
    from chat.mcp_routes import LogosClient
    from yandex_client_modules.client import YandexResponsesClient

    sent = []
    monkeypatch.setattr(
        YandexResponsesClient, "ask_with_mcp", lambda self, **kw: sent.append(kw) or {}
    )
    monkeypatch.setattr(
        provider.ClaudeOAuthTransport, "ask_with_mcp", lambda *a, **kw: pytest.fail("Claude used")
    )
    result = object.__new__(LogosClient).ask_with_mcp("test", "logos-runtime")
    assert result == {} and sent[0]["model_key"] == "logos-runtime"


def test_unknown_model_fails_without_yandex_fallback(offline):
    from chat.mcp_routes import LogosClient

    with pytest.raises(provider.ClaudeOAuthError, match="not configured"):
        object.__new__(LogosClient).ask_with_mcp("test", "claude-unknown")
    assert not offline.sent


def test_chat_http_preserves_invocation_and_unknown_cost(
    offline, tool_registry, monkeypatch, tmp_path
):
    import chat.mcp_routes as routes
    from storage import db

    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "http.memory"))
    db.init_db()
    db.create_conversation("conv-http", "Claude", MODEL)
    monkeypatch.setattr(routes, "get_current_owner_id", lambda required=False: None)
    app = Flask(__name__)
    app.register_blueprint(routes.mcp_bp)
    response = app.test_client().post(
        "/api/chat",
        json={
            "conversation_id": "conv-http",
            "model": MODEL,
            "message": "проверка",
            "params": {"active_tool_categories": []},
        },
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["reply"] == "Готово" and data["cost"] is None
    assert data["invocation_id"] and data["trace_id"]
    assert data["trace"]["context"]["invocation_id"] == data["invocation_id"]
    assert SECRET not in json.dumps(data)


def test_native_claude_factory_does_not_seed_yandex(monkeypatch):
    from agent_runtime import issue_runner
    from chat.mcp_routes import LogosClient

    monkeypatch.setattr(
        issue_runner, "seed_provider_credential", lambda: pytest.fail("Yandex seed used")
    )
    assert isinstance(issue_runner._default_client_factory(MODEL)(), LogosClient)


def test_continuation_is_scoped_to_conversation_and_call_ids(offline):
    call = {"type": "tool_use", "id": "toolu1", "name": "inspect", "input": {}}
    offline.replies = [envelope(call, stop="tool_use")]
    client = provider.ClaudeOAuthTransport()
    client.ask("test", MODEL, "first")
    for conversation, identity in (("other", "toolu1"), ("first", "wrong")):
        with pytest.raises(provider.ClaudeOAuthError):
            client.ask(
                "",
                MODEL,
                conversation,
                {"input": [{"type": "function_call_output", "call_id": identity, "output": "ok"}]},
            )
    assert len(offline.sent) == 1


def test_cli_failure_trace_does_not_capture_process_output(monkeypatch):
    monkeypatch.setattr(
        provider.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout=SECRET, stderr=SECRET),
    )
    trace = ExecutionTrace()
    try:
        provider._cli_output("ant", [], {"ENV_SECRET": SECRET})
    except provider.ClaudeOAuthError as error:
        trace.record_error("claude_oauth", str(error), exception=error)
    assert SECRET not in json.dumps(trace.finalize())


def test_model_substitution_cannot_execute_tools(offline, tool_registry):
    from chat.mcp_routes import LogosClient

    reply = envelope(
        {"type": "tool_use", "id": "tool1", "name": "inspect", "input": {}}, stop="tool_use"
    )
    reply["model"] = "claude-other"
    offline.replies = [reply]
    with pytest.raises(provider.ClaudeOAuthError, match="does not match"):
        object.__new__(LogosClient).ask_with_mcp(
            "test", MODEL, params={"active_tool_categories": ["fixture"]}
        )
    assert not tool_registry and len(offline.sent) == 1


