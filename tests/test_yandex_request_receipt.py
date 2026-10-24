"""Offline proofs that the receipt describes the assembled POST, not config."""

from __future__ import annotations

import copy
import hashlib
import json
from types import SimpleNamespace

import pytest
import requests

from observability.trace_manager import ExecutionTrace
from yandex_client_modules import request_mixin
from yandex_client_modules.request_mixin import YandexRequestMixin
from yandex_client_modules.request_receipt import build_request_manifest


def fingerprint(value):
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return {"canonical_sha256": hashlib.sha256(raw).hexdigest(), "canonical_bytes": len(raw)}


def manifests(trace):
    return [
        event["payload"]
        for event in trace.trace["events"]
        if event["type"] == "api_request_manifest"
    ]


@pytest.fixture
def transport(monkeypatch):
    monkeypatch.setattr(
        requests.Session,
        "request",
        lambda *args, **kwargs: pytest.fail("receipt tests must never call a live provider"),
    )
    credential = SimpleNamespace(project_id="synthetic-project", trace_key_id="synthetic-key")
    monkeypatch.setattr(request_mixin, "_resolve_global_provider_credential", lambda *_: credential)
    monkeypatch.setattr(request_mixin, "_resolve_quota_user_id", lambda *_: None)
    monkeypatch.setattr(request_mixin, "reserve_request", lambda *_: None)
    trace = ExecutionTrace(trace_id="receipt-trace")
    posts = []

    def post(url, *, json, timeout, allow_redirects):
        assert allow_redirects is False
        # Capture the actual json= object exactly at the transport boundary. The
        # receipt must already exist before this fake replaces the network.
        posts.append(copy.deepcopy(json))
        receipt = manifests(trace)[-1]["manifest"]
        assert receipt["payload"] == fingerprint(json)
        return SimpleNamespace(
            status_code=200,
            json=lambda: {"id": f"response-{len(posts)}", "status": "completed", "output": []},
            raise_for_status=lambda: None,
        )

    class Client(YandexRequestMixin):
        responses_url = "https://provider.invalid/responses"
        session = SimpleNamespace(post=post)

        @staticmethod
        def _resolve_yandex_conv_id(_conversation_id):
            return "provider-conversation"

        @staticmethod
        def _log_request(*args, **kwargs):
            return None

        @staticmethod
        def _log_response(response):
            return response

    return Client(), trace, posts


def test_receipt_matches_actual_payload_after_normalization_and_tool_cleaning(transport):
    client, trace, posts = transport
    secret = "synthetic-mcp-authorization-value"
    text = "Unicode данные 🔎\n" * 1000 + "\npassword=synthetic-password"
    client.ask(
        text,
        "logos-runtime",
        conversation_id="local-conversation",
        params={
            "background": True,
            "store": False,
            "temperature": "0.6",
            "max_output_tokens": "12345",
            "reasoning_effort": "high",
            "truncation": "disabled",
            "service_tier": "priority",
            "tool_choice": "auto",
            "tools": [
                {"type": "mcp", "server_label": "invalid-no-url"},
                {
                    "type": "mcp",
                    "server_url": " https://example.invalid/mcp ",
                    "authorization": secret,
                    "headers": {"Authorization": "Bearer synthetic-header"},
                },
                {"type": "function", "name": "read_file", "parameters": {"type": "object"}},
            ],
        },
        execution_trace=trace,
    )
    event = manifests(trace)[0]
    receipt = event["manifest"]
    payload = posts[0]
    assert receipt["capture_stage"] == "assembled_payload_before_post"
    assert receipt["wire_bytes_verified"] is False
    assert receipt["canonicalization"] == "python-json-sorted-utf8-compact-v1"
    assert receipt["model"]["value"] == payload["model"]
    assert receipt["settings"]["temperature"]["value"] == 0.6
    assert receipt["settings"]["store"]["value"] is True
    assert receipt["settings"]["max_output_tokens"]["value"] == 12345
    assert receipt["settings"]["parallel_tool_calls"]["value"] is True
    assert receipt["settings"]["reasoning"]["effort"] == "high"
    assert receipt["tools"]["count"] == 2
    assert receipt["tools"]["types"]["mcp"] == receipt["tools"]["types"]["function"] == 1
    assert receipt["input"]["item_count"] == receipt["input"]["item_types"]["message"] == 1
    assert receipt["input"]["canonical_bytes"] > 4000
    assert receipt["payload"] != fingerprint(trace.trace["api_requests"][0]["payload"])
    assert event["step"] == 1
    assert event["correlation_id"] == trace.get_step_correlation_id(1)
    assert receipt["provider_effective"] == {"state": "unknown"}
    rendered = json.dumps(receipt, ensure_ascii=False)
    assert secret not in rendered and "synthetic-header" not in rendered
    assert "synthetic-password" not in rendered and text not in rendered
    saved = [
        event["payload"]["manifest"]
        for event in trace.finalize()["events"]
        if event["type"] == "api_request_manifest"
    ]
    assert saved == [receipt]


def test_continuation_linkage_is_the_actual_input_and_conversation(transport):
    client, trace, posts = transport
    client.ask("initial", "logos-runtime", conversation_id="local", execution_trace=trace)
    continuation = [
        {"type": "reasoning", "encrypted_content": "synthetic-reasoning"},
        {"type": "function_call", "call_id": "call-1", "name": "read_file", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "call-1", "output": "synthetic-private-output"},
    ]
    client.ask(
        "unused fallback message",
        "logos-runtime",
        conversation_id="local",
        params={"input": continuation, "previous_response_id": "not-forwarded-by-builder"},
        execution_trace=trace,
    )
    first, second = manifests(trace)
    assert (first["step"], second["step"]) == (1, 2)
    receipt = second["manifest"]
    assert posts[1]["input"] == continuation
    assert receipt["input"] | fingerprint(continuation) == receipt["input"]
    assert receipt["input"]["item_types"]["function_call"] == 1
    assert receipt["input"]["item_types"]["function_call_output"] == 1
    assert receipt["input"]["call_linkage"]["count"] == 2
    assert receipt["linkage"]["conversation"] == first["manifest"]["linkage"]["conversation"]
    assert (
        receipt["linkage"]["conversation"] | fingerprint(posts[1]["conversation"])
        == receipt["linkage"]["conversation"]
    )
    assert receipt["linkage"]["previous_response_id"] == {"state": "absent"}
    assert (
        receipt["linkage"]["prompt_cache_key"] | fingerprint("local")
        == receipt["linkage"]["prompt_cache_key"]
    )
    rendered = json.dumps(receipt)
    for value in (
        "synthetic-reasoning",
        "synthetic-private-output",
        "call-1",
        "provider-conversation",
    ):
        assert value not in rendered


def test_missing_options_remain_not_sent_despite_local_params(transport):
    client, trace, posts = transport
    client.ask(
        "text",
        "logos-runtime",
        params={
            "temperature": None,
            "max_output_tokens": 0,
            "stream": True,
            "tool_choice": "required",
        },
        execution_trace=trace,
    )
    receipt = manifests(trace)[0]["manifest"]
    for key in ("temperature", "top_p", "max_output_tokens", "tool_choice", "stream"):
        assert key not in posts[0]
        assert receipt["settings"][key] == {"state": "absent"}
    assert receipt["tools"] == {"state": "absent"}
    assert receipt["instructions"] == {"state": "absent"}


def test_fingerprint_changes_with_actual_content_and_options_not_key_order():
    payload = {"model": "gpt://project/model/latest", "input": [{"role": "user", "content": "one"}]}
    baseline = build_request_manifest(payload)
    assert baseline == build_request_manifest(dict(reversed(list(payload.items()))))
    changed = copy.deepcopy(payload)
    changed["input"][0]["content"] = "two"
    assert build_request_manifest(changed)["payload"] != baseline["payload"]
    changed = {**payload, "temperature": 0.9}
    assert build_request_manifest(changed)["payload"] != baseline["payload"]
    assert payload["input"][0]["content"] == "one"


def test_only_safe_model_and_setting_values_are_exported():
    sensitive = "Bearer synthetic-never-export"
    payload = {
        "model": "gpt://project/sk-synthetic-secret/latest",
        "input": sensitive,
        "instructions": sensitive,
        "tools": [{"type": sensitive, "authorization": sensitive, "headers": {"x-key": sensitive}}],
        "metadata": {"private": sensitive},
        "text": {"format": {"schema": {"description": sensitive}}},
        "prompt": {"variables": {"secret": sensitive}},
        "reasoning": {"effort": sensitive},
        "service_tier": sensitive,
        "tool_choice": {"name": sensitive},
        "temperature": sensitive,
        "store": sensitive,
    }
    receipt = build_request_manifest(payload)
    assert sensitive not in json.dumps(receipt)
    assert "sk-synthetic-secret" not in json.dumps(receipt)
    assert "value" not in receipt["model"]
    assert receipt["settings"]["temperature"]["state"] == "present"
    assert "value" not in receipt["settings"]["temperature"]
    assert receipt["tools"]["types"]["other"] == 1


def test_serializable_large_integer_does_not_require_lossy_float_conversion():
    value = 10**400
    payload = {"input": "text", "max_output_tokens": value}
    receipt = build_request_manifest(payload)
    assert receipt["payload"] == fingerprint(payload)
    assert receipt["settings"]["max_output_tokens"]["value"] == value


def test_capture_occurs_after_logging_immediately_before_post(transport):
    client, trace, posts = transport

    def final_request_hook(*args, **kwargs):
        kwargs["json"]["temperature"] = 0.31

    client._log_request = final_request_hook
    client.ask("text", "logos-runtime", execution_trace=trace)
    assert (
        posts[0]["temperature"]
        == manifests(trace)[0]["manifest"]["settings"]["temperature"]["value"]
        == 0.31
    )


def test_requests_without_trace_do_not_compute_or_export_receipt(transport, monkeypatch):
    client, _trace, _posts = transport
    monkeypatch.setattr(request_mixin, "build_request_manifest", lambda *_: pytest.fail("no trace"))
    client.session = SimpleNamespace(
        post=lambda *_args, **_kwargs: SimpleNamespace(
            json=lambda: {"status": "completed"}, raise_for_status=lambda: None
        )
    )
    assert client.ask("text", "logos-runtime")["status"] == "completed"


def test_private_callback_preserves_complete_initial_and_continuation_input_before_post(transport):
    client, trace, posts = transport
    saved = []
    text = "Full private Unicode input 🧭\n" * 1000
    instructions = "Full private instructions\n" * 500
    original_post = client.session.post

    def post(*args, **kwargs):
        assert len(saved) == len(posts) + 1
        assert saved[-1]["data"]["input"] == kwargs["json"]["input"]
        return original_post(*args, **kwargs)

    def checkpoint(snapshot):
        saved.append(copy.deepcopy(snapshot))
        # A trusted writer still gets an isolated copy, not a mutable handle
        # to the provider request or the exported manifest.
        snapshot["data"]["input"].clear()
        snapshot["manifest"]["payload"].clear()

    client.session = SimpleNamespace(post=post)
    tools = [
        {
            "type": "mcp",
            "server_url": "https://example.invalid",
            "authorization": "private-tool-authorization",
        }
    ]
    client.ask(
        text,
        "logos-runtime",
        conversation_id="local",
        params={"instructions": instructions, "tools": tools, "on_provider_input": checkpoint},
        execution_trace=trace,
    )
    continuation = [{"type": "function_call_output", "call_id": "call-1", "output": text}]
    client.ask(
        "unused",
        "logos-runtime",
        conversation_id="local",
        params={"input": continuation, "on_provider_input": checkpoint},
        execution_trace=trace,
    )
    assert [item["step"] for item in saved] == [1, 2]
    for index, item in enumerate(saved):
        assert item["schema_version"] == 1
        assert item["capture_stage"] == "assembled_payload_before_post"
        assert item["trace_id"] == trace.trace_id
        assert item["correlation_id"] == trace.get_step_correlation_id(index + 1)
        assert item["manifest"]["payload"] == fingerprint(posts[index])
        assert "on_provider_input" not in posts[index]
        assert set(item["data"]) <= {
            "input",
            "instructions",
            "conversation",
            "previous_response_id",
        }
        assert "private-tool-authorization" not in json.dumps(item)
        assert (
            len(item["data"]["input"][0].get("content", item["data"]["input"][0].get("output", "")))
            > 4000
        )
    assert saved[0]["data"]["instructions"] == instructions
    assert saved[1]["data"]["input"] == continuation
    assert posts[0]["input"][0]["content"] == text
    assert posts[1]["input"] == continuation
    assert all("data" not in event for event in manifests(trace))


def test_private_callback_failure_prevents_post_and_continuation(transport):
    client, trace, posts = transport

    def unavailable(snapshot):
        raise OSError("private input storage unavailable")

    with pytest.raises(OSError, match="private input storage"):
        client.ask(
            "initial",
            "logos-runtime",
            params={"on_provider_input": unavailable},
            execution_trace=trace,
        )
    assert posts == []
    assert manifests(trace)[0]["manifest"]["provider_effective"] == {"state": "unknown"}
    assert trace.trace.get("api_requests", []) == []
    assert not any(event["type"] == "api_request_sent" for event in trace.trace["events"])

    client.ask("initial", "logos-runtime", execution_trace=trace)
    with pytest.raises(OSError, match="private input storage"):
        client.ask(
            "continue",
            "logos-runtime",
            params={
                "input": [{"type": "function_call_output", "call_id": "old", "output": "private"}],
                "on_provider_input": unavailable,
            },
            execution_trace=trace,
        )
    assert len(posts) == 1


def test_invalid_private_callback_is_rejected_before_credentials_or_post(transport, monkeypatch):
    client, trace, posts = transport
    touched = []
    monkeypatch.setattr(
        request_mixin,
        "_resolve_global_provider_credential",
        lambda *_: touched.append("credentials"),
    )
    with pytest.raises(ValueError, match="on_provider_input"):
        client.ask(
            "text",
            "logos-runtime",
            params={"on_provider_input": "not-callable"},
            execution_trace=trace,
        )
    assert touched == posts == []


def test_private_writer_timeout_is_not_misreported_as_provider_failure(transport):
    client, trace, posts = transport
    failure = requests.Timeout("private writer timeout; no request was sent")

    def unavailable(snapshot):
        raise failure

    with pytest.raises(requests.Timeout) as caught:
        client.ask(
            "text",
            "logos-runtime",
            params={"on_provider_input": unavailable},
            execution_trace=trace,
        )
    assert caught.value is failure
    assert posts == []
    assert trace.trace.get("api_requests", []) == []
    assert not any(event["type"] == "api_request_error" for event in trace.trace["events"])
    assert not any(event["type"] == "api_request_sent" for event in trace.trace["events"])


def test_private_callback_without_trace_reports_unknown_linkage(transport):
    client, _trace, _posts = transport
    saved = []
    outgoing = []

    def post(*args, **kwargs):
        outgoing.append(copy.deepcopy(kwargs["json"]))
        return SimpleNamespace(json=lambda: {"status": "completed"}, raise_for_status=lambda: None)

    client.session = SimpleNamespace(post=post)
    client.ask("text", "logos-runtime", params={"on_provider_input": saved.append})
    assert saved[0]["step"] is saved[0]["trace_id"] is saved[0]["correlation_id"] is None
    assert saved[0]["data"] == {
        name: outgoing[0][name]
        for name in ("input", "instructions", "conversation", "previous_response_id")
        if name in outgoing[0]
    }


@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_non_json_numbers_cannot_acquire_an_exact_fingerprint(value):
    with pytest.raises(ValueError):
        build_request_manifest({"input": "text", "temperature": value})
