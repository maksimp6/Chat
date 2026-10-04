from unittest.mock import MagicMock, patch

import pytest

import mcp_routes
from invocation.idempotency import IdempotencyInProgress, IdempotencyStore, validate_key


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_store_replays_success_and_releases_failures():
    store = IdempotencyStore()
    effect = MagicMock(side_effect=["first", "second"])
    assert store.run("k", effect).replayed is False
    replay = store.run("k", effect)
    assert (replay.value, replay.replayed) == ("first", True)
    assert effect.call_count == 1

    failing = MagicMock(side_effect=["bad", "good"])
    assert store.run("f", failing, is_success=lambda v: v == "good").value == "bad"
    assert store.run("f", failing, is_success=lambda v: v == "good").value == "good"
    assert failing.call_count == 2


def test_store_releases_key_when_effect_raises():
    store = IdempotencyStore()
    with pytest.raises(RuntimeError):
        store.run("k", MagicMock(side_effect=RuntimeError("boom")))
    assert store.run("k", lambda: "ok").value == "ok"


def test_store_rejects_concurrent_duplicate():
    store = IdempotencyStore()

    def effect():
        with pytest.raises(IdempotencyInProgress):
            store.run("k", lambda: "duplicate")
        return "original"

    assert store.run("k", effect).value == "original"


def test_store_is_bounded_and_expires():
    clock = FakeClock()
    store = IdempotencyStore(max_entries=2, ttl_seconds=10, clock=clock)
    for key in ("a", "b", "c"):
        store.run(key, lambda key=key: key)
    assert store.run("a", lambda: "a2").replayed is False, "oldest entry is evicted"

    clock.now = 100.0
    assert store.run("c", lambda: "c2").replayed is False, "expired entry runs again"


def test_store_validates_configuration_and_keys():
    with pytest.raises(ValueError):
        IdempotencyStore(max_entries=0)
    with pytest.raises(ValueError):
        IdempotencyStore(ttl_seconds=0)
    for bad in ("", "   ", "x" * 201, None, 5):
        with pytest.raises(ValueError):
            validate_key(bad)


@pytest.fixture()
def client():
    from app import app

    app.config["TESTING"] = True
    with (
        patch.object(mcp_routes, "APPROVED_EXECUTIONS", IdempotencyStore()),
        app.test_client() as test_client,
    ):
        yield test_client


def _post(client, **extra):
    body = {"conversation_id": "conv", "name": "tool", "arguments": {}, **extra}
    return client.post("/api/mcp/execute-approved", json=body)


def test_same_key_executes_once_and_replays(client):
    run = MagicMock(return_value=({"reply": "done"}, 200))
    with patch.object(mcp_routes, "_run_approved_tool", run):
        first = _post(client, idempotency_key="conv:call-1")
        second = _post(client, idempotency_key="conv:call-1")
    assert run.call_count == 1
    assert first.get_json() == {"reply": "done", "replayed": False}
    assert second.status_code == 200
    assert second.get_json() == {"reply": "done", "replayed": True}


def test_failed_execution_can_be_retried_with_same_key(client):
    run = MagicMock(side_effect=[({"error": "denied"}, 403), ({"reply": "ok"}, 200)])
    with patch.object(mcp_routes, "_run_approved_tool", run):
        assert _post(client, idempotency_key="k").status_code == 403
        assert _post(client, idempotency_key="k").status_code == 200
    assert run.call_count == 2


def test_requests_without_key_keep_previous_behaviour(client):
    run = MagicMock(return_value=({"reply": "ok"}, 200))
    with patch.object(mcp_routes, "_run_approved_tool", run):
        assert _post(client).get_json() == {"reply": "ok"}
        _post(client)
    assert run.call_count == 2


def test_invalid_key_and_in_progress_and_crash(client):
    assert _post(client, idempotency_key="").status_code == 400
    with patch.object(
        mcp_routes.APPROVED_EXECUTIONS, "run", side_effect=IdempotencyInProgress("k")
    ):
        response = _post(client, idempotency_key="k")
    assert (response.status_code, response.get_json()) == (
        409,
        {"error": "approved_action_in_progress"},
    )
    with patch.object(mcp_routes, "_run_approved_tool", side_effect=RuntimeError("secret")):
        crashed = _post(client, idempotency_key="crash")
    assert crashed.status_code == 500
    assert "secret" not in crashed.get_data(as_text=True)


def _run_tool(exec_result, data, tool_meta=True):
    executor = MagicMock()
    executor.return_value.execute.return_value = exec_result
    alice = MagicMock()
    alice.return_value.extract_text.return_value = "Готово"
    alice.return_value.extract_usage.return_value = {"total_tokens": 3}
    add_message = MagicMock()
    meta = {"name": "tool"} if tool_meta else None
    with (
        patch.object(mcp_routes.registry, "get_tool_meta", return_value=meta),
        patch.object(mcp_routes, "UniversalToolExecutor", executor),
        patch.object(mcp_routes, "AliceClient", alice),
        patch.object(mcp_routes, "calculate_full_cost", return_value=0.5),
        patch.object(mcp_routes, "add_message", add_message),
        patch.object(mcp_routes, "get_current_owner_id", return_value="owner"),
    ):
        payload, status = mcp_routes._run_approved_tool(data)
    return payload, status, executor, add_message


def test_run_approved_tool_success_records_reply_once():
    data = {"conversation_id": "c", "name": "set_ui_theme", "current_theme": " Dark "}
    payload, status, executor, add_message = _run_tool({"success": True, "data": {"ok": 1}}, data)
    assert status == 200
    assert payload["reply"] == "Готово" and payload["cost"] == 0.5
    add_message.assert_called_once_with("c", "assistant", "Готово", cost=0.5)
    call = executor.return_value.execute.call_args.args[0]
    assert call.arguments["current_theme"] == "dark"


def test_run_approved_tool_error_paths():
    assert _run_tool({}, {"name": "ghost"}, tool_meta=False)[1] == 400
    denied = {"success": False, "error": "no", "metadata": {"phase": "authorization"}}
    assert _run_tool(denied, {"name": "tool"})[1] == 403
    payload, status, _, add_message = _run_tool({"success": False}, {"name": "tool"})
    assert (status, payload["error"]) == (400, "Tool execution failed")
    add_message.assert_not_called()
