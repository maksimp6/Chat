"""Outbound admission: real registry, fake final wire, and zero-send controls."""

from __future__ import annotations

import ast
import io
import json
from pathlib import Path
from unittest.mock import Mock
from urllib.request import Request

import pytest

from api.contracts import ContractViolation
from api.yandex_contracts import IncompleteYandexContract
from api.yandex_transport import (
    _NoRedirect,
    yandex_connection_request,
    yandex_request,
    yandex_urlopen,
)

ROOT = Path(__file__).resolve().parents[1]
TOKENIZE = "https://llm.api.cloud.yandex.net/foundationModels/v1/tokenize"
MODELS = "https://ai.api.cloud.yandex.net/v1/models"


def test_valid_documented_request_reaches_transport_once_and_denies_redirects():
    send = Mock(return_value="response")
    body = {"modelUri": "gpt://folder/model", "text": "hello"}
    assert yandex_request(send, "POST", TOKENIZE, json=body, timeout=3) == "response"
    send.assert_called_once_with(TOKENIZE, json=body, timeout=3, allow_redirects=False)
    assert send.call_args.kwargs["json"] is not body


@pytest.mark.parametrize(
    "url,kwargs",
    [
        (TOKENIZE, {"json": {"text": 42}}),
        (TOKENIZE, {"json": {"unexpected": "secret-marker"}}),
        (TOKENIZE + "?extra=secret-marker", {"json": {"text": "hello"}}),
        (TOKENIZE, {"json": {"text": "hello"}, "params": {"extra": "secret-marker"}}),
        ("https://ai.api.cloud.yandex.net/v1/responses", {"json": {"model": 42, "input": "hello"}}),
        ("https://ai.api.cloud.yandex.net/v1/responses/id?unknown=secret-marker", {}),
        ("https://ai.api.cloud.yandex.net/v1/nonexistent", {}),
        ("https://iam.api.cloud.yandex.net/iam/v1/apiKeys", {"json": {"unexpected": True}}),
        ("https://stt.api.cloud.yandex.net/speech/v1/stt:recognize", {"data": b""}),
        ("https://api.yandex-ai-studio.ru/v1/logs", {"json": {}}),
        (
            "https://ai.api.cloud.yandex.net/v1/realtime/client-events/input_audio_buffer.append",
            {"json": {}},
        ),
    ],
)
def test_rejected_request_has_zero_network_calls_and_no_payload_disclosure(url, kwargs):
    send = Mock()
    with pytest.raises(ContractViolation) as exc:
        yandex_request(send, "POST" if kwargs else "GET", url, **kwargs)
    send.assert_not_called()
    assert "secret-marker" not in str(exc.value)


def test_no_request_body_and_unknown_query_are_validated():
    send = Mock()
    yandex_request(send, "GET", MODELS)
    send.assert_called_once()
    send.reset_mock()
    for kwargs in ({"json": {}}, {"params": {"unexpected": "x"}}):
        with pytest.raises(ContractViolation):
            yandex_request(send, "GET", MODELS, **kwargs)
        send.assert_not_called()


def test_form_encoding_and_multipart_fail_before_stream_consumption():
    stream = io.BytesIO(b"file-secret")
    send = Mock()
    with pytest.raises(ContractViolation):
        yandex_request(
            send,
            "POST",
            "https://ai.api.cloud.yandex.net/v1/files",
            files={"file": stream},
            data={"purpose": "invalid"},
        )
    assert stream.tell() == 0
    send.assert_not_called()
    with pytest.raises(IncompleteYandexContract):
        yandex_request(send, "POST", TOKENIZE, data={"text": "hello"})
    send.assert_not_called()


def test_configured_ai_proxy_is_explicit_and_cannot_escape_to_another_origin():
    send = Mock()
    yandex_request(
        send, "GET", "https://proxy.example/custom/models", base_url="https://proxy.example/custom"
    )
    send.assert_called_once()
    for url in (
        "https://foreign.example/custom/models",
        "http://proxy.example/custom/models",
        "https://proxy.example/elsewhere/models",
        "https://user:password@proxy.example/custom/models",
    ):
        send.reset_mock()
        with pytest.raises(ContractViolation):
            yandex_request(send, "GET", url, base_url="https://proxy.example/custom")
        send.assert_not_called()


def test_path_injection_and_duplicate_query_rejected_before_network():
    send = Mock()
    for suffix in ("%2Fother", "..", "%5Cother"):
        with pytest.raises(ContractViolation):
            yandex_request(send, "GET", "https://ai.api.cloud.yandex.net/v1/files/" + suffix)
    with pytest.raises(ContractViolation):
        yandex_request(send, "GET", MODELS + "?x=a&x=b")
    send.assert_not_called()


def test_http_client_raw_json_and_urllib_pass_only_validated_data(monkeypatch):
    connection = Mock()
    body = json.dumps({"text": "hello"}).encode()
    yandex_connection_request(
        connection,
        "llm.api.cloud.yandex.net",
        "POST",
        "/foundationModels/v1/tokenize",
        body=body,
        headers={"Content-Type": "application/json"},
    )
    connection.request.assert_called_once()
    connection.reset_mock()
    with pytest.raises(ContractViolation):
        yandex_connection_request(
            connection,
            "llm.api.cloud.yandex.net",
            "POST",
            "/foundationModels/v1/tokenize",
            body=b'{"text": 42}',
            headers={"Content-Type": "application/json"},
        )
    connection.request.assert_not_called()
    opener = Mock()
    monkeypatch.setattr("api.yandex_transport.build_opener", lambda *handlers: opener)
    yandex_urlopen(
        Request(TOKENIZE, data=body, headers={"Content-Type": "application/json"}), timeout=3
    )
    opener.open.assert_called_once()
    opener.reset_mock()
    with pytest.raises(ContractViolation):
        yandex_urlopen(
            Request(TOKENIZE, data=b'{"text":42}', headers={"Content-Type": "application/json"}),
            timeout=3,
        )
    opener.open.assert_not_called()


def test_redirect_handler_never_follows_a_location():
    with pytest.raises(ContractViolation, match="redirects"):
        _NoRedirect().redirect_request(None, None, 307, "", {}, "https://other.example")


def test_requests_redirect_is_an_explicit_block_after_one_send():
    send = Mock(return_value=Mock(status_code=307))
    with pytest.raises(ContractViolation, match="redirect"):
        yandex_request(send, "GET", MODELS)
    send.assert_called_once_with(MODELS, allow_redirects=False)


def test_profiles_are_explicit_complete_schemas_with_primary_sources():
    from jsonschema import Draft7Validator

    from api.yandex_contracts import schema_gaps
    from api.yandex_request_profiles import PROFILES

    assert len(PROFILES) == 22
    for entry in PROFILES.values():
        Draft7Validator.check_schema(entry["schema"])
        assert entry["source"]["url"].startswith(
            ("https://aistudio.yandex.ru/", "https://yandex.cloud/")
        )
        for reference in entry["request"].values():
            assert schema_gaps(entry["schema"], reference) == []


def test_prepared_requests_wire_uses_validated_payload_and_invalid_never_sends():
    import requests

    session = requests.Session()
    wire = []

    def send(prepared, **options):
        wire.append((prepared, options))
        response = requests.Response()
        response.status_code = 200
        response._content = b"{}"
        return response

    session.send = send
    payload = {
        "model": "gpt://folder/model",
        "input": [{"role": "user", "content": "hello"}],
        "max_output_tokens": 4096,
    }
    yandex_request(
        session.post, "POST", "https://ai.api.cloud.yandex.net/v1/responses", json=payload
    )
    assert len(wire) == 1
    assert json.loads(wire[0][0].body) == payload
    assert wire[0][1]["allow_redirects"] is False
    with pytest.raises(ContractViolation):
        yandex_request(
            session.post,
            "POST",
            "https://ai.api.cloud.yandex.net/v1/responses",
            json={**payload, "input": [{"role": "user", "content": 42}]},
        )
    assert len(wire) == 1


def test_upload_metadata_checked_without_consuming_stream_and_raw_multipart():
    from scripts.semantic_pipeline import _multipart_file

    stream = io.BytesIO(b"# source")
    send = Mock()
    yandex_request(
        send,
        "POST",
        "https://ai.api.cloud.yandex.net/v1/files",
        files={"file": ("source.md", stream)},
        data={"purpose": "assistants"},
    )
    assert stream.tell() == 0
    send.assert_called_once()
    raw, content_type = _multipart_file("source.md", b"# source")
    connection = Mock()
    yandex_connection_request(
        connection,
        "ai.api.cloud.yandex.net",
        "POST",
        "/v1/files",
        body=raw,
        headers={"Content-Type": content_type},
    )
    connection.request.assert_called_once()
    connection.reset_mock()
    with pytest.raises(ContractViolation):
        yandex_connection_request(
            connection,
            "ai.api.cloud.yandex.net",
            "POST",
            "/v1/files",
            body=raw.replace(b"assistants", b"invalid-purpose"),
            headers={"Content-Type": content_type},
        )
    connection.request.assert_not_called()


def test_json_null_and_connection_target_cannot_bypass_validation():
    connection = Mock()
    with pytest.raises(ContractViolation):
        yandex_connection_request(
            connection,
            "llm.api.cloud.yandex.net",
            "POST",
            "/foundationModels/v1/tokenize",
            body=b"null",
            headers={"Content-Type": "application/json"},
        )
    connection.request.assert_not_called()
    connection.host = "other.example"
    with pytest.raises(ContractViolation, match="host"):
        yandex_connection_request(connection, "ai.api.cloud.yandex.net", "GET", "/v1/models")
    connection.request.assert_not_called()


def test_cross_field_chunking_and_form_size_limits_reject_before_send():
    send = Mock()
    with pytest.raises(ContractViolation, match="overlap"):
        yandex_request(
            send,
            "POST",
            "https://ai.api.cloud.yandex.net/v1/vector_stores",
            json={
                "chunking_strategy": {
                    "type": "static",
                    "static": {"max_chunk_size_tokens": 100, "chunk_overlap_tokens": 51},
                }
            },
        )
    with pytest.raises(ContractViolation, match="form body"):
        yandex_request(
            send,
            "POST",
            "https://tts.api.cloud.yandex.net/speech/v1/tts:synthesize",
            data={"text": "я" * 5000},
        )
    send.assert_not_called()


def test_mixed_github_provider_helper_cannot_bypass_using_wrong_flag(monkeypatch):
    from scripts.pr_court import PROVIDER, request

    raw = Mock()
    monkeypatch.setattr("scripts.pr_court.urllib.request.urlopen", raw)
    with pytest.raises(ContractViolation):
        request(PROVIDER, "synthetic", {"model": 42, "input": "hello"}, provider=False)
    raw.assert_not_called()


def test_main_client_rejects_before_quota_trace_and_socket(monkeypatch):
    from types import SimpleNamespace

    from yandex_client_modules.request_mixin import YandexRequestMixin

    client = YandexRequestMixin()
    client.base_url = "https://ai.api.cloud.yandex.net/v1"
    client.responses_url = client.base_url + "/responses"
    client.session = Mock()
    client._resolve_yandex_conv_id = Mock(return_value=None)
    client._log_request = Mock()
    reserve = Mock()
    monkeypatch.setattr("yandex_client_modules.request_mixin.reserve_request", reserve)
    monkeypatch.setattr(
        "yandex_client_modules.request_mixin._resolve_global_provider_credential",
        lambda *args: SimpleNamespace(project_id="folder"),
    )
    with pytest.raises(ContractViolation):
        client.ask("hello", "logos-runtime", params={"input": [{"role": "user", "content": 42}]})
    reserve.assert_not_called()
    client._log_request.assert_not_called()
    client.session.post.assert_not_called()


DEDICATED = (
    "yandex_client_modules/request_mixin.py",
    "yandex_client_modules/conversations.py",
    "yandex_client_modules/polling_mixin.py",
    "yandex_client_modules/response_poller.py",
    "yandex_client_modules/sdk.py",
    "yandex_client_modules/api_key_provider.py",
    "provider_accounts/model_discovery.py",
    "agent_memory/extractor.py",
    "chat/voice_routes.py",
    "workspace/files.py",
    "scripts/send_logs.py",
)


def unguarded_calls(source: str) -> list[str]:
    direct = {
        "requests.get",
        "requests.post",
        "requests.delete",
        "requests.request",
        "self.session.get",
        "self.session.post",
        "self.session.request",
        "session.get",
        "conn.request",
        "connection.request",
    }
    return [
        ast.unparse(node.func)
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call) and ast.unparse(node.func) in direct
    ]


def test_dedicated_yandex_clients_cannot_bypass_boundary():
    for relative in DEDICATED:
        assert unguarded_calls((ROOT / relative).read_text()) == [], relative
    # Negative control: the architecture check detects an actual direct bypass.
    assert unguarded_calls("requests.post(url, json=payload)") == ["requests.post"]


def test_no_http_client_bypass_in_runner_model_functions():
    functions = {
        "scripts/semantic_pipeline.py": {"yandex_call", "yandex_api_request"},
        "scripts/q14_ai_review.py": {"make_transport"},
        "scripts/q14_observer.py": {"request"},
        "scripts/rag_pipeline.py": {"yandex_embeddings"},
    }
    for relative, names in functions.items():
        tree = ast.parse((ROOT / relative).read_text())
        selected = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
        assert len(selected) == len(names)
        for function in selected:
            assert unguarded_calls(ast.unparse(function)) == [], relative
