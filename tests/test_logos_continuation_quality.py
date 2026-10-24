"""Offline controls for long native work, approval boundaries and transport waits."""

from types import SimpleNamespace

import pytest
import requests

from agent_runtime.responses_tool_loop import run_tool_loop
from chat import mcp_routes
from chat.mcp_routes import LogosClient
from observability.trace_manager import ExecutionTrace
from yandex_client_modules import request_mixin
from yandex_client_modules.client import YandexResponsesClient
from yandex_client_modules.errors import YandexClientError


def tool_response(number, name="read_file"):
    return {
        "id": f"response-{number}",
        "status": "completed",
        "output": [
            {"type": "reasoning", "summary": [{"type": "summary_text", "text": "state"}]},
            {
                "type": "function_call",
                "call_id": f"call-{number}",
                "name": name,
                "arguments": '{"path":"README.md"}',
            },
        ],
    }


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    def reject(*args, **kwargs):
        pytest.fail("Continuation controls must not call a live provider")

    monkeypatch.setattr(requests.Session, "request", reject)


def test_unbounded_explicit_rounds_complete_twenty_five_real_continuations():
    executed = []
    inputs = []
    final = {"id": "response-final", "output": [{"type": "message", "content": []}]}

    def continue_request(items, previous):
        inputs.append(items)
        count = len(inputs)
        # Every continuation carries the exact reasoning/output and one tool result.
        assert items[:-1] == previous["output"]
        assert items[-1]["call_id"] == f"call-{count}"
        return final if count == 25 else tool_response(count + 1)

    result = run_tool_loop(
        tool_response(1),
        lambda call: executed.append(call["call_id"]) or {"read": True},
        continue_request,
        max_rounds=None,
    )
    assert result is final
    assert executed == [f"call-{i}" for i in range(1, 26)]
    assert len(inputs) == 25


def test_default_round_ceiling_still_stops_without_executing_pending_round():
    executed = []
    continued = []

    def continue_request(items, previous):
        continued.append(previous["id"])
        return tool_response(len(continued) + 1)

    with pytest.raises(RuntimeError, match="max_rounds"):
        run_tool_loop(
            tool_response(1),
            lambda call: executed.append(call["call_id"]),
            continue_request,
        )
    assert executed == [f"call-{i}" for i in range(1, 17)]
    assert len(continued) == 16


@pytest.mark.parametrize("limit", [True, -1, 1.5, "16"])
def test_invalid_round_limit_has_no_tool_side_effects(limit):
    executed = []
    with pytest.raises(ValueError, match="max_rounds"):
        run_tool_loop(tool_response(1), executed.append, lambda *_: {}, max_rounds=limit)
    assert executed == []


def test_later_approval_stops_entire_batch_without_executing_any_pending_tool(monkeypatch):
    sent = []
    executed = []
    blocked = tool_response(2)
    blocked["output"].append(
        {
            "type": "function_call",
            "call_id": "protected-call",
            "name": "apply_patch",
            "arguments": '{"path":"README.md","search_text":"a","replace_text":"b"}',
        }
    )
    responses = iter([tool_response(1), blocked])

    def transport(self, **kwargs):
        sent.append(kwargs)
        return next(responses)

    monkeypatch.setattr(YandexResponsesClient, "ask_with_mcp", transport)
    monkeypatch.setattr(mcp_routes.mcp_storage, "list_servers", list)
    monkeypatch.setattr(
        LogosClient,
        "_execute_single_tool",
        lambda self, call, servers, trace=None: (
            executed.append(call["call_id"]) or {"result": "source"}
        ),
    )
    params = {"max_rounds": None, "request_timeout": 900, "poll_timeout": 7200}
    result = object.__new__(LogosClient).ask_with_mcp("work", "model", params=params)
    assert result is blocked
    assert executed == ["call-1"]
    assert len(sent) == 2
    assert sent[1]["params"]["max_rounds"] is None
    assert sent[1]["params"]["request_timeout"] == 900
    assert sent[1]["params"]["poll_timeout"] == 7200
    assert sent[1]["params"]["input"][:-1] == tool_response(1)["output"]


@pytest.fixture
def request_client(monkeypatch):
    credential = SimpleNamespace(project_id="test-project", trace_key_id="test-key")
    monkeypatch.setattr(request_mixin, "_resolve_global_provider_credential", lambda *_: credential)
    monkeypatch.setattr(request_mixin, "_resolve_quota_user_id", lambda *_: None)
    monkeypatch.setattr(request_mixin, "reserve_request", lambda *_: None)
    client = object.__new__(YandexResponsesClient)
    client.responses_url = "https://provider.invalid/responses"
    client._resolve_yandex_conv_id = lambda _: None
    client._log_request = lambda *_args, **_kwargs: None
    client._log_response = lambda response: response
    return client


def provider_response(payload):
    return SimpleNamespace(status_code=200, json=lambda: payload, raise_for_status=lambda: None)


def test_explicit_maximum_waits_are_actual_transport_arguments_and_not_wire_fields(
    request_client,
):
    posts = []
    waits = []

    def post(url, **kwargs):
        posts.append(kwargs)
        return provider_response({"id": "known-response", "status": "queued"})

    def wait(response_id, **kwargs):
        waits.append((response_id, kwargs))
        return {"id": response_id, "status": "completed"}

    request_client.session = SimpleNamespace(post=post)
    request_client._wait = wait
    trace = ExecutionTrace()
    result = request_client.ask(
        "work",
        "model",
        params={"background": True, "request_timeout": 900, "poll_timeout": 7200},
        execution_trace=trace,
    )
    assert result["status"] == "completed"
    assert posts[0]["timeout"] == 900
    assert waits[0][0] == "known-response"
    assert waits[0][1]["timeout"] == 7200
    assert "request_timeout" not in posts[0]["json"]
    assert "poll_timeout" not in posts[0]["json"]
    assert trace.finalize()["responses"][0]["response_id"] == "known-response"


@pytest.mark.parametrize("status", ["completed", "incomplete", "failed", "cancelled"])
def test_terminal_background_post_never_polls_or_repeats_generation(request_client, status):
    posts = []
    payload = {"id": "already-terminal-response", "status": status}

    def post(*args, **kwargs):
        posts.append(kwargs)
        return provider_response(payload)

    def forbidden(*args, **kwargs):
        pytest.fail("A terminal POST must not trigger GET polling")

    request_client.session = SimpleNamespace(post=post, get=forbidden)
    request_client._wait = forbidden
    if status in {"failed", "cancelled"}:
        with pytest.raises(YandexClientError, match=f"Task status: {status}"):
            request_client.ask("work", "model", params={"background": True})
    else:
        assert request_client.ask("work", "model", params={"background": True}) is payload
    assert len(posts) == 1


@pytest.mark.parametrize(
    "params",
    [
        {"request_timeout": 901},
        {"request_timeout": 0},
        {"request_timeout": True},
        {"request_timeout": float("nan")},
        {"request_timeout": "900"},
        {"poll_timeout": 7201},
        {"poll_timeout": -1},
        {"poll_timeout": float("inf")},
    ],
)
def test_invalid_timeout_rejected_before_credentials_or_post(request_client, monkeypatch, params):
    touched = []
    monkeypatch.setattr(
        request_mixin,
        "_resolve_global_provider_credential",
        lambda *_: touched.append("credentials"),
    )
    request_client.session = SimpleNamespace(post=lambda *_a, **_k: touched.append("POST"))
    with pytest.raises(ValueError, match="timeout"):
        request_client.ask("work", "model", params=params)
    assert touched == []


def test_post_timeout_never_issues_an_automatic_duplicate(request_client):
    posts = []

    def post(*args, **kwargs):
        posts.append(kwargs)
        raise requests.Timeout("ambiguous POST outcome")

    request_client.session = SimpleNamespace(post=post)
    with pytest.raises(YandexClientError, match="ambiguous POST"):
        request_client.ask("work", "model", params={"request_timeout": 900})
    assert len(posts) == 1


def test_known_response_is_retained_when_background_polling_times_out(request_client):
    posts = []

    def post(*args, **kwargs):
        posts.append(kwargs)
        return provider_response({"id": "recoverable-response", "status": "in_progress"})

    def wait(*args, **kwargs):
        raise YandexClientError("poll timeout")

    request_client.session = SimpleNamespace(post=post)
    request_client._wait = wait
    trace = ExecutionTrace()
    with pytest.raises(YandexClientError, match="poll timeout"):
        request_client.ask(
            "work",
            "model",
            params={"background": True, "request_timeout": 900, "poll_timeout": 7200},
            execution_trace=trace,
        )
    assert len(posts) == 1
    assert trace.finalize()["responses"][0]["response_id"] == "recoverable-response"


def test_checkpoint_receives_queued_id_before_polling_can_fail(request_client):
    posts = []
    saved = []
    queued = {"id": "checkpoint-before-wait", "status": "queued"}

    def post(*args, **kwargs):
        posts.append(kwargs)
        return provider_response(queued)

    def wait(*args, **kwargs):
        assert saved == [queued]
        raise YandexClientError("poll timeout")

    request_client.session = SimpleNamespace(post=post)
    request_client._wait = wait
    with pytest.raises(YandexClientError, match="poll timeout"):
        request_client.ask(
            "work", "model", params={"background": True, "on_provider_response": saved.append}
        )
    assert saved == [queued]
    assert len(posts) == 1
    assert "on_provider_response" not in posts[0]["json"]


@pytest.mark.parametrize("queued_first", [False, True])
def test_checkpoint_receives_terminal_result_exactly_once(request_client, queued_first):
    posts = []
    saved = []
    queued = {"id": "checkpoint-final", "status": "queued"}
    terminal = {"id": "checkpoint-final", "status": "completed", "output": []}

    def post(*args, **kwargs):
        posts.append(kwargs)
        return provider_response(queued if queued_first else terminal)

    def wait(*args, **kwargs):
        assert queued_first
        assert saved == [queued]
        return terminal

    request_client.session = SimpleNamespace(post=post)
    request_client._wait = wait
    result = request_client.ask(
        "work", "model", params={"background": True, "on_provider_response": saved.append}
    )
    assert result is terminal
    assert saved == ([queued, terminal] if queued_first else [terminal])
    assert len(posts) == 1


def test_invalid_checkpoint_callback_is_rejected_before_credentials(request_client, monkeypatch):
    touched = []
    monkeypatch.setattr(
        request_mixin,
        "_resolve_global_provider_credential",
        lambda *_: touched.append("credentials"),
    )
    with pytest.raises(ValueError, match="on_provider_response"):
        request_client.ask("work", "model", params={"on_provider_response": "save"})
    assert touched == []


def test_checkpoint_failure_propagates_without_poll_or_repeated_post(request_client):
    posts = []

    def post(*args, **kwargs):
        posts.append(kwargs)
        return provider_response({"id": "checkpoint-write-failed", "status": "queued"})

    def checkpoint(response):
        assert response["id"] == "checkpoint-write-failed"
        raise OSError("checkpoint unavailable")

    def forbidden(*args, **kwargs):
        pytest.fail("A failed checkpoint must not continue or start another generation")

    request_client.session = SimpleNamespace(post=post)
    request_client._wait = forbidden
    with pytest.raises(OSError, match="checkpoint unavailable"):
        request_client.ask("work", "model", params={"on_provider_response": checkpoint})
    assert len(posts) == 1
