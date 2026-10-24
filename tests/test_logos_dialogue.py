"""Offline checks of the native Logos dialogue, transport, and persisted trace."""

import hashlib
import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
import requests

from chat.mcp_routes import LogosClient
from core.config import Config
from scripts import logos_context
from scripts import logos_dialogue as dialogue
from scripts.logos_quality import OWNER_DIRECTIVE, REQUESTED_OUTPUT_TOKENS
from tool_providers import filesystem


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    # Permit this module's fake-only runtime tests. Production admission is
    # exercised independently in test_logos_launch_hold.py.
    monkeypatch.setattr(dialogue, "enforce_native_launch_hold", lambda: None)

    def reject(*args, **kwargs):
        pytest.fail("The dialogue tests must never use a live provider")

    monkeypatch.setattr(requests.Session, "request", reject)


@pytest.fixture(autouse=True)
def owner_permitted_generation(monkeypatch):
    # These tests model a permitted run; the closed default is in test_logos_generation_gate.py.
    monkeypatch.setenv("LOGOS_GENERATION_ALLOWED", "true")


@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "AGENTS.md").write_text("Use the existing runtime and report actual evidence.\n")
    (root / "README.md").write_text("Logos repository context.\n")
    for name in logos_context.REQUIRED_FILES:
        path = root / name
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"Complete committed guidance: {name}\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        cwd=root,
        check=True,
    )
    monkeypatch.setattr(filesystem, "BASE_DIR", str(root))
    return root


def prepared_context(repo):
    """Build a complete real packet; replace only the external GitHub transport."""
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    api = "/repos/maksimp6/Chat"
    data = {
        f"{api}/issues/1413": {
            "id": 1413,
            "title": "Canonical task",
            "body": "Full acceptance criteria for native Logos.",
            "comments": 1,
        },
        f"{api}/pulls/1435": {
            "id": 1435,
            "title": "Native Logos dialogue",
            "body": "Full PR description and runtime evidence.",
            "comments": 1,
            "review_comments": 2,
            "head": {"sha": head},
            "base": {"sha": "b" * 40},
        },
        f"{api}/issues/1413/comments?per_page=100&page=1": [
            {"id": 1, "body": "Task discussion with current criteria."}
        ],
        f"{api}/issues/1435/comments?per_page=100&page=1": [
            {"id": 2, "body": "PR discussion: retain all context and approvals."}
        ],
        f"{api}/pulls/1435/reviews?per_page=100&page=1": [
            {"id": 3, "body": "Review body: verify actual tool results.", "state": "COMMENTED"}
        ],
        f"{api}/pulls/1435/comments?per_page=100&page=1": [
            {"id": 4, "body": "Inline parent finding.", "path": "README.md"}
        ],
        f"{api}/pulls/1435/comments?per_page=100&page=2": [
            {"id": 5, "body": "Inline reply with evidence.", "in_reply_to_id": 4}
        ],
    }

    def get_json(path):
        if path in data:
            return data[path]
        assert "?per_page=100&page=" in path, path
        return []

    packet = logos_context.build_context(
        repo,
        owner_context="Current owner: strongest compatible model, full tools and context; no merge.",
        include_files=("README.md",),
        get_json=get_json,
    )
    assert packet["case"]["counts"] == {
        "issue_comment": 1,
        "pr_comment": 1,
        "review": 1,
        "inline_comment": 2,
    }
    return packet, {
        "status": "indexed",
        "context_sha256": packet["packet_sha256"],
        "vector_store_ids": ["offline-project-store"],
        "checkout_sha": head,
        "generation_requests": 0,
    }


def run_prepared_dialogue(question, *, repo_root, **kwargs):
    packet, knowledge = prepared_context(repo_root)
    return dialogue.run_dialogue(
        question, repo_root=repo_root, context_packet=packet, knowledge_receipt=knowledge, **kwargs
    )


def prepared_files(repo, directory):
    packet, knowledge = prepared_context(repo)
    context_file = directory / "context.json"
    knowledge_file = directory / "knowledge.json"
    context_file.write_text(json.dumps(packet), encoding="utf-8")
    knowledge_file.write_text(json.dumps(knowledge), encoding="utf-8")
    return ["--context-file", str(context_file), "--knowledge-file", str(knowledge_file)]


def native_response(number, *, read=False):
    output = (
        [
            {
                "type": "function_call",
                "name": "read_file",
                "call_id": f"read-{number}",
                "arguments": json.dumps({"path": "README.md", "length": 100}),
            }
        ]
        if read
        else [
            {
                "type": "message",
                "content": [
                    {"type": "output_text", "text": "Ответ Logos из тестового транспорта."}
                ],
            }
        ]
    )
    return {
        "id": f"offline-response-{number}",
        "status": "completed",
        "output": output,
        "usage": {"input_tokens": 11, "output_tokens": 3, "total_tokens": 14},
    }


@pytest.fixture
def native_transport(monkeypatch):
    from agent_runtime.issue_runner import seed_provider_credential

    monkeypatch.setenv("ALICE_PROVIDER_CREDENTIAL_KEY", "offline-credential-encryption-key")
    monkeypatch.setenv("YANDEX_API_KEY", "offline-yandex-key")
    monkeypatch.setenv("YANDEX_PROJECT_ID", "offline-project")
    seed_provider_credential()

    class Response:
        status_code = 200
        url = "https://ai.api.cloud.yandex.net/v1/responses"

        def __init__(self, data):
            self.data = data

        def json(self):
            return self.data

        def raise_for_status(self):
            pass

    def setup(*responses):
        client = LogosClient(Config)
        calls = []
        provider_responses = iter([{"id": "00000000-0000-4000-8000-000000000001"}, *responses])

        def post(url, *, json, timeout):
            calls.append((url, json))
            return Response(next(provider_responses))

        monkeypatch.setattr(client.session, "post", post)
        return client, calls

    return setup


@pytest.fixture
def stored_response_transport(monkeypatch, native_transport):
    def setup(payload, *, status_code=200):
        client, _ = native_transport()
        calls = []
        response = requests.Response()
        response.status_code = status_code
        response._content = json.dumps(payload).encode("utf-8")
        response.headers["Content-Type"] = "application/json"

        def get(url, *, timeout):
            calls.append((url, timeout))
            response.url = url
            return response

        def forbidden(*args, **kwargs):
            pytest.fail("Reading a stored response must not generate or execute tools")

        monkeypatch.setattr(client.session, "get", get)
        monkeypatch.setattr(client.session, "post", forbidden)
        monkeypatch.setattr(client, "create_conversation", forbidden)
        monkeypatch.setattr(client, "ask_with_mcp", forbidden)
        monkeypatch.setattr(client, "_execute_single_tool", forbidden)
        return client, calls

    return setup


def test_native_dialogue_reads_repository_and_preserves_answer_usage_and_trace(
    repo, native_transport
):
    from invocation.manager import get_invocation

    client, calls = native_transport(native_response(1, read=True), native_response(2))
    result = run_prepared_dialogue(
        "почему распнули иисуса",
        repo_root=repo,
        client_factory=lambda: client,
    )

    assert result["status"] == "completed"
    assert result["reply"] == "Ответ Logos из тестового транспорта."
    assert len(calls) == 3 and calls[0][0].endswith("/conversations")
    assert result["generation_requests"] == 2
    assert result["response_ids"] == ["offline-response-1", "offline-response-2"]
    assert result["usage"] == {"input_tokens": 22, "output_tokens": 6, "total_tokens": 28}
    assert result["cost"]["provider_confirmed"] is False
    assert result["tool_calls"] == [{"name": "read_file", "call_id": "read-1", "success": True}]
    invocation = get_invocation(result["invocation_id"])
    assert invocation["status"] == "completed"
    assert invocation["trace"]["trace_id"] == result["trace_id"]
    assert invocation["session_id"] == result["session_id"]
    assert "offline-yandex-key" not in json.dumps(result)
    assert calls[2][1]["input"][-1]["type"] == "function_call_output"
    assert "Logos repository context." in calls[2][1]["input"][-1]["output"]
    assert calls[1][1]["instructions"] == dialogue.DIALOGUE_INSTRUCTIONS
    assert calls[2][1]["instructions"] == dialogue.DIALOGUE_INSTRUCTIONS
    assert OWNER_DIRECTIVE in calls[1][1]["instructions"]
    for payload in (calls[1][1], calls[2][1]):
        assert payload["reasoning"] == {"effort": "high"}
        assert payload["background"] is True
        assert payload["truncation"] == "disabled"
        assert payload["max_output_tokens"] == REQUESTED_OUTPUT_TOKENS
    available = {tool.get("name") for tool in calls[1][1]["tools"]}
    assert {"read_file", "write_file", "run_command"} <= available
    assert {"web_search", "file_search", "code_interpreter"} <= {
        tool["type"] for tool in calls[1][1]["tools"]
    }
    initial = result["capability_receipt"]["initial_input"]
    assert initial["context_status"] == "complete"
    assert initial["context_manifest"]["source_count"] == (
        len(logos_context.REQUIRED_FILES) + len(logos_context.OPTIONAL_FILES) + 9
    )


def test_native_command_requires_approval_without_running_child(
    repo, monkeypatch, native_transport
):
    executed = []
    from tool_providers.registry import registry

    monkeypatch.setitem(registry._tools["run_command"], "func", lambda *_: executed.append(1))
    response = native_response(1)
    response["output"] = [
        {
            "type": "function_call",
            "name": "run_command",
            "call_id": "command-1",
            "arguments": json.dumps({"command": "exit 0"}),
        }
    ]
    client, calls = native_transport(response)
    result = run_prepared_dialogue(
        "Run the local check and report its exit code.",
        repo_root=repo,
        client_factory=lambda: client,
    )
    assert result["status"] == "needs_approval"
    assert result["pending_tools"] == ["run_command"]
    assert result["generation_requests"] == 1
    assert result["tool_calls"] == []
    assert executed == []
    assert len(calls) == 2  # conversation creation + one model request


@pytest.mark.parametrize("pending_tool", [False, True])
def test_inspect_stored_response_uses_one_get_without_generation_or_tool_execution(
    stored_response_transport, pending_tool
):
    response_id = "541186dc-6749-4466-830d-511f762830e8"
    payload = native_response(1, read=pending_tool)
    payload["id"] = response_id
    client, calls = stored_response_transport(payload)

    result = dialogue.inspect_response(response_id, client_factory=lambda: client)

    assert result["status"] == "retrieved"
    assert result["provider_status"] == "completed"
    assert result["response_ids"] == [response_id]
    assert result["generation_requests"] == 0
    assert result["retrieval_requests"] == 1
    assert result["tool_calls"] == []
    assert calls == [(client.responses_url + "/" + response_id, 15)]


def test_inspect_stored_failure_exposes_machine_diagnostics_without_provider_message(
    stored_response_transport,
):
    response_id = "541186dc-6749-4466-830d-511f762830e8"
    private_message = "provider body contains private credential details"
    client, _ = stored_response_transport(
        {
            "id": response_id,
            "status": "failed",
            "error": {
                "code": "server_error",
                "type": "provider_error",
                "message": private_message,
            },
            "incomplete_details": {"reason": "max_output_tokens"},
        }
    )

    result = dialogue.inspect_response(response_id, client_factory=lambda: client)

    assert result["status"] == "retrieved" and result["provider_status"] == "failed"
    assert result["provider_error_code"] == "server_error"
    assert result["provider_error_type"] == "provider_error"
    assert result["provider_reason"] == "max_output_tokens"
    assert result["generation_requests"] == 0
    assert private_message not in json.dumps(result)


@pytest.mark.parametrize("pending", [False, True])
def test_dialogue_nonnull_provider_error_is_not_completed_or_approval(repo, pending):
    class Client:
        def create_conversation(self, trace):
            return {"id": "offline-conversation"}

        def ask_with_mcp(self, **kwargs):
            response = native_response(1, read=pending)
            response["error"] = {"code": "server_error", "message": "private-body"}
            return response

    result = run_prepared_dialogue("Review", repo_root=repo, client_factory=Client)
    assert result["status"] == "failed"
    assert result["error"] == "ProviderResponseError"
    assert result["provider_error_present"] is True
    assert "private-body" not in json.dumps(result)


def test_dialogue_preserves_exact_unicode_reply_and_provider_echo(repo, tmp_path):
    reply = "  Логос 🧪\n" * 1000 + " \n"
    out = tmp_path / "result.json"

    class Client:
        def create_conversation(self, trace):
            return {"id": "offline-conversation"}

        def ask_with_mcp(self, **kwargs):
            response = {
                "id": "63a39a54-527a-44c4-af15-4946468e6e87",
                "status": "completed",
                "model": "test-model",
                "temperature": 0.3,
                "truncation": "disabled",
                "reasoning": {"effort": "high"},
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": reply}],
                    }
                ],
            }
            kwargs["params"]["on_provider_response"](response)
            checkpoint = json.loads(out.read_text(encoding="utf-8"))
            assert checkpoint["reply"] == reply
            assert checkpoint["reply_sha256"] == hashlib.sha256(reply.encode("utf-8")).hexdigest()
            return response

    result = run_prepared_dialogue(
        "Review", repo_root=repo, client_factory=Client, checkpoint_file=out
    )
    assert result["status"] == "completed"
    assert result["reply"] == reply
    assert result["capability_receipt"]["provider_echo"]["temperature"] == 0.3
    assert result["provider_echo"]["reasoning"] == {"effort": "high"}


def test_provider_echo_does_not_export_secret_model_or_invalid_settings():
    result = {}
    dialogue._provider_diagnostics(
        result,
        {
            "model": "ghp_synthetic-private-token",
            "max_output_tokens": True,
            "max_tool_calls": -1,
            "temperature": float("nan"),
            "reasoning": {"effort": "private\nvalue"},
            "truncation": "private\nvalue",
        },
    )
    assert "provider_echo" not in result


@pytest.mark.parametrize("provider_status", ["completed", "incomplete"])
def test_inspect_exports_full_assistant_reply_before_persistence(
    stored_response_transport, monkeypatch, provider_status
):
    response_id = "541186dc-6749-4466-830d-511f762830e8"
    reply = "  Ёж🙂\n" + "Полный ответ. " * 500 + "\t\n"
    client, calls = stored_response_transport(
        {
            "id": response_id,
            "status": provider_status,
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": reply}],
                }
            ],
        }
    )
    original = dialogue.persist_invocation_trace
    persisted = []

    def observe_persistence(*args):
        persisted.append(args)
        return original(*args)

    monkeypatch.setattr(dialogue, "persist_invocation_trace", observe_persistence)
    result = dialogue.inspect_response(response_id, client_factory=lambda: client)

    assert result["status"] == "retrieved"
    assert result["provider_status"] == provider_status
    assert result["reply"] == reply
    assert result["reply_state"] == "available"
    assert result["reply_chars"] == len(reply)
    assert result["reply_sha256"] == hashlib.sha256(reply.encode("utf-8")).hexdigest()
    assert result["generation_requests"] == 0
    assert len(calls) == len(persisted) == 1


@pytest.mark.parametrize(
    ("fields", "expected", "state"),
    [
        ({"output_text": ""}, "", "empty"),
        ({"output_text": "\t\n"}, "\t\n", "available"),
        ({"output_text": "Top-level Ё"}, "Top-level Ё", "available"),
        ({"output": [{"type": "output_text", "text": "Direct"}]}, "Direct", "available"),
        (
            {"output": [{"type": "message", "content": [{"type": "text", "text": ""}]}]},
            "",
            "empty",
        ),
        ({}, "", "unknown"),
        ({"output": []}, "", "unknown"),
        ({"output": None, "output_text": 42}, "", "unknown"),
        ({"output": [{"type": "function_call", "name": "read_file"}]}, "", "unknown"),
    ],
)
def test_inspect_distinguishes_explicit_text_from_missing_text(
    stored_response_transport, fields, expected, state
):
    response_id = "541186dc-6749-4466-830d-511f762830e8"
    client, _ = stored_response_transport({"id": response_id, "status": "completed", **fields})

    result = dialogue.inspect_response(response_id, client_factory=lambda: client)

    assert result["reply"] == expected and result["reply_state"] == state
    if state == "unknown":
        assert "reply_chars" not in result and "reply_sha256" not in result
    else:
        assert result["reply_chars"] == len(expected)
        assert result["reply_sha256"] == hashlib.sha256(expected.encode("utf-8")).hexdigest()


def test_inspect_excludes_reasoning_other_roles_and_diagnostic_text(stored_response_transport):
    response_id = "541186dc-6749-4466-830d-511f762830e8"
    private = "PRIVATE-CONTENT-NOT-ASSISTANT-OUTPUT"
    client, _ = stored_response_transport(
        {
            "id": response_id,
            "status": "completed",
            "text": private,
            "error": {"message": private},
            "output": [
                {"type": "reasoning", "content": [{"type": "text", "text": private}]},
                {"type": "message", "role": "tool", "content": [private]},
                {"type": "output_text", "role": "user", "text": private},
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {"type": "reasoning_text", "text": private},
                        {"type": "output_text", "text": "Первая"},
                        " часть.\n",
                        {"type": "text", "text": "Вторая. "},
                    ],
                },
            ],
        }
    )

    result = dialogue.inspect_response(response_id, client_factory=lambda: client)

    assert result["reply"] == "Первая часть.\nВторая. "
    assert private not in json.dumps(result)


def test_inspect_retains_reply_when_trace_persistence_fails(stored_response_transport, monkeypatch):
    from storage.memory_db import DatabaseError

    response_id = "541186dc-6749-4466-830d-511f762830e8"
    reply = " Полный ответ после отказа хранилища.\n"
    client, calls = stored_response_transport(
        {"id": response_id, "status": "completed", "output_text": reply}
    )

    def fail_persistence(*args):
        raise DatabaseError("private database details")

    monkeypatch.setattr(dialogue, "persist_invocation_trace", fail_persistence)
    result = dialogue.inspect_response(response_id, client_factory=lambda: client)

    assert result["status"] == "failed" and result["persistence"]["status"] == "failed"
    assert result["reply"] == reply and result["reply_state"] == "available"
    assert result["response_ids"] == [response_id]
    assert result["reply_sha256"] == hashlib.sha256(reply.encode("utf-8")).hexdigest()
    assert result["generation_requests"] == 0 and len(calls) == 1
    assert "private database" not in json.dumps(result)


@pytest.mark.parametrize("response_id", ["../../responses?secret=private", None])
def test_inspect_rejects_invalid_uuid_before_initializing_client(response_id):
    result = dialogue.inspect_response(
        response_id,
        client_factory=lambda: pytest.fail("Invalid response ID initialized the provider"),
    )

    assert result["status"] == "failed" and result["error"] == "ValueError"
    assert result["generation_requests"] == result["retrieval_requests"] == 0
    assert "secret=private" not in json.dumps(result)


@pytest.mark.parametrize("stored_id", ["00000000-0000-4000-8000-000000000001", None, 123])
def test_inspect_rejects_a_different_stored_response(stored_response_transport, stored_id):
    client, calls = stored_response_transport(
        {"id": stored_id, "status": "completed", "output_text": "Wrong response content"}
    )

    result = dialogue.inspect_response(
        "541186dc-6749-4466-830d-511f762830e8", client_factory=lambda: client
    )

    assert result["status"] == "failed" and result["error"] == "ValueError"
    assert result["generation_requests"] == 0 and len(calls) == 1
    assert result["reply_state"] == "unknown" and result["reply"] == ""
    assert "Wrong response content" not in json.dumps(result)


@pytest.mark.parametrize("status_code", [401, 404])
def test_inspect_http_error_reports_status_without_provider_body(
    stored_response_transport, status_code
):
    private_message = "Bearer private-test-credential"
    client, calls = stored_response_transport(
        {"error": {"code": "unauthenticated", "message": private_message}},
        status_code=status_code,
    )

    result = dialogue.inspect_response(
        "541186dc-6749-4466-830d-511f762830e8", client_factory=lambda: client
    )

    assert result["status"] == "failed" and result["http_status"] == status_code
    assert result["generation_requests"] == 0 and len(calls) == 1
    assert result["reply_state"] == "unknown" and result["reply"] == ""
    assert private_message not in json.dumps(result)


def test_inspect_rejects_secret_like_and_non_token_diagnostics(stored_response_transport):
    response_id = "541186dc-6749-4466-830d-511f762830e8"
    client, _ = stored_response_transport(
        {
            "id": response_id,
            "status": "failed",
            "error": {"code": "ghp_private-test-token", "type": "private\nmessage"},
            "incomplete_details": {"reason": {"private": "data"}},
        }
    )

    result = dialogue.inspect_response(response_id, client_factory=lambda: client)

    assert result["status"] == "retrieved" and result["provider_status"] == "failed"
    assert not {"provider_error_code", "provider_error_type", "provider_reason"} & result.keys()
    assert "private" not in json.dumps(result)


@pytest.mark.parametrize("read_existing", [False, True])
def test_persistence_failure_produces_safe_result(
    repo, stored_response_transport, monkeypatch, read_existing
):
    from storage.memory_db import DatabaseError

    def fail_persistence(*args, **kwargs):
        raise DatabaseError("private database details")

    monkeypatch.setattr(dialogue, "persist_invocation_trace", fail_persistence)
    if read_existing:
        response_id = "541186dc-6749-4466-830d-511f762830e8"
        client, _ = stored_response_transport({"id": response_id, "status": "completed"})
        result = dialogue.inspect_response(response_id, client_factory=lambda: client)
    else:
        result = run_prepared_dialogue("question", repo_root=repo, client_factory=OfflineClient)

    assert result["status"] == "failed" and result["error"] == "DatabaseError"
    assert "private database" not in json.dumps(result)


class OfflineClient:
    def __init__(self, response=None):
        self.response = native_response(1) if response is None else response
        self.calls = []

    def create_conversation(self, trace):
        return {"id": "00000000-0000-4000-8000-000000000001"}

    def ask_with_mcp(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def test_dialogue_uses_native_tools_and_repository_rules_without_extra_call_caps(repo):
    client = OfflineClient()

    result = run_prepared_dialogue(
        "Вопрос пользователя", repo_root=repo, client_factory=lambda: client
    )

    assert result["status"] == "completed"
    request = client.calls[0]
    assert request["model_key"] == dialogue.DEFAULT_MODEL == "deepseek-v4.1-flash"
    assert "Вопрос пользователя" in request["message"]
    assert "Use the existing runtime and report actual evidence." in request["message"]
    from tool_providers.registry import registry

    assert request["params"]["active_tool_categories"] == list(registry.get_available_categories())
    assert request["params"]["max_rounds"] is None
    assert (
        request["params"]["tools_config"]["file_search"]["vector_store_ids"]
        == "offline-project-store"
    )
    assert request["params"]["reasoning"] == {"effort": "high"}
    assert "max_tool_calls" not in request["params"]
    assert request["params"]["max_output_tokens"] == REQUESTED_OUTPUT_TOKENS
    assert request["params"]["truncation"] == "disabled"
    assert OWNER_DIRECTIVE in request["params"]["instructions"]


def test_pending_native_tool_is_reported_as_needing_approval(repo):
    client = OfflineClient(
        {
            "output": [
                {
                    "type": "function_call",
                    "name": "protected_tool",
                    "call_id": "pending-1",
                    "arguments": "{}",
                }
            ],
        }
    )

    result = run_prepared_dialogue("question", repo_root=repo, client_factory=lambda: client)

    assert result["status"] == "needs_approval"
    assert result["pending_tools"] == ["protected_tool"]
    assert result["reply"] == ""


def test_failed_dialogue_sanitizes_provider_exception_and_persists_failure(repo):
    from invocation.manager import get_invocation
    from yandex_client_modules.errors import YandexClientError

    class FailedClient(OfflineClient):
        def create_conversation(self, trace):
            raise YandexClientError("private provider details must not escape")

    result = run_prepared_dialogue("question", repo_root=repo, client_factory=FailedClient)

    assert result["status"] == "failed" and result["error"] == "YandexClientError"
    assert "private provider details" not in json.dumps(result)
    invocation = get_invocation(result["invocation_id"])
    assert invocation["status"] == "failed"
    assert "private provider details" not in json.dumps(invocation["trace"])


def test_default_client_initializes_existing_credential_and_native_client(repo, monkeypatch):
    from agent_runtime import issue_runner
    from chat import mcp_routes
    from core.config import Config as current_config

    monkeypatch.setenv("ALICE_PROVIDER_CREDENTIAL_KEY", "offline-key")
    seeded = []
    configurations = []
    monkeypatch.setattr(issue_runner, "seed_provider_credential", lambda: seeded.append(True))

    def native_client(config):
        configurations.append(config)
        return OfflineClient()

    monkeypatch.setattr(mcp_routes, "LogosClient", native_client)
    result = run_prepared_dialogue("question", repo_root=repo)

    assert result["status"] == "completed"
    assert seeded == [True] and configurations == [current_config]


def test_empty_question_rejected_before_client_creation(repo):
    result = run_prepared_dialogue(
        " \n",
        repo_root=repo,
        client_factory=lambda: pytest.fail("empty question invoked provider"),
    )

    assert result["status"] == "failed" and result["reply"] == ""


def test_incomplete_response_preserves_partial_reply_without_claiming_completion(repo):
    from invocation.manager import get_invocation

    response = native_response(1)
    response["status"] = "incomplete"
    client = OfflineClient(response)

    result = run_prepared_dialogue("question", repo_root=repo, client_factory=lambda: client)

    assert result["status"] == "incomplete" and result["error"] == "IncompleteResponse"
    assert result["reply"] == "Ответ Logos из тестового транспорта."
    assert result["provider_status"] == "incomplete"
    assert get_invocation(result["invocation_id"])["status"] == "failed"


def test_empty_answer_is_failed_and_missing_usage_stays_unknown(repo):
    client = OfflineClient({"id": "empty-response", "status": "completed", "output": []})

    result = run_prepared_dialogue("question", repo_root=repo, client_factory=lambda: client)

    assert result["status"] == "failed" and result["reply"] == ""
    assert result["usage"] == {}
    assert result["cost"] == {"status": "unknown", "provider_confirmed": False}


def test_cli_writes_actual_result_with_verified_context_files(repo, tmp_path, monkeypatch, capsys):
    question = tmp_path / "question.txt"
    question.write_text("вопрос пользователя", encoding="utf-8")
    output = tmp_path / "answer.json"
    captured = []
    reply = 'ответ\n::error::literal text\r$(touch escaped) "quote"'

    def run(question_text, *, context_packet, knowledge_receipt, checkpoint_file):
        assert checkpoint_file == output
        captured.append((question_text, context_packet, knowledge_receipt))
        return {"status": "completed", "reply": reply, "generation_requests": 1}

    monkeypatch.setattr(dialogue, "run_dialogue", run)

    assert (
        dialogue.main(
            [
                "--question-file",
                str(question),
                "--out",
                str(output),
                *prepared_files(repo, tmp_path),
            ]
        )
        == 0
    )
    question_text, packet, knowledge = captured[0]
    assert question_text == "вопрос пользователя"
    assert logos_context.validate_context(packet) == packet
    assert knowledge["context_sha256"] == packet["packet_sha256"]
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["reply"] == reply
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1 and lines[0].startswith("LOGOS_RESULT=")
    assert json.loads(lines[0].removeprefix("LOGOS_RESULT=")) == result


def test_cli_response_id_reads_existing_response_without_dialogue(tmp_path, monkeypatch, capsys):
    response_id = "541186dc-6749-4466-830d-511f762830e8"
    output = tmp_path / "answer.json"
    captured = []
    expected = {"status": "retrieved", "generation_requests": 0, "response_ids": [response_id]}

    def inspect(value):
        captured.append(value)
        return expected

    monkeypatch.setattr(dialogue, "inspect_response", inspect)
    monkeypatch.setattr(
        dialogue, "run_dialogue", lambda *args, **kwargs: pytest.fail("Read mode generated")
    )

    assert dialogue.main(["--response-id", response_id, "--out", str(output)]) == 0
    assert captured == [response_id]
    assert json.loads(output.read_text()) == expected
    assert json.loads(capsys.readouterr().out.removeprefix("LOGOS_RESULT=")) == expected


def test_cli_rejects_mixing_response_retrieval_and_new_question(tmp_path):
    with pytest.raises(SystemExit) as exc:
        dialogue.main(
            [
                "--response-id",
                "541186dc-6749-4466-830d-511f762830e8",
                "--question-file",
                str(tmp_path / "question.txt"),
                "--out",
                str(tmp_path / "answer.json"),
            ]
        )

    assert exc.value.code == 2
    assert not (tmp_path / "answer.json").exists()


def test_cli_empty_response_id_fails_without_question_fallback(tmp_path, monkeypatch):
    output = tmp_path / "answer.json"
    monkeypatch.setattr(
        dialogue, "run_dialogue", lambda *args, **kwargs: pytest.fail("Read mode generated")
    )

    assert dialogue.main(["--response-id", "", "--out", str(output)]) == 1
    result = json.loads(output.read_text())
    assert result["error"] == "ValueError"
    assert result["generation_requests"] == result["retrieval_requests"] == 0


def test_cli_missing_input_fails_safely_and_writes_status(repo, tmp_path, capsys):
    output = tmp_path / "answer.json"

    assert (
        dialogue.main(
            [
                "--question-file",
                str(tmp_path / "private-missing-input.txt"),
                "--out",
                str(output),
                *prepared_files(repo, tmp_path),
            ]
        )
        == 1
    )

    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["status"] == "failed" and result["error"] == "FileNotFoundError"
    assert "private-missing-input" not in json.dumps(result) + capsys.readouterr().out


def test_runner_roots_resolve_to_repository_not_package():
    from agent_runtime import issue_runner

    assert dialogue.ROOT == issue_runner.ROOT == Path(__file__).resolve().parents[1]
    assert (dialogue.ROOT / "app.py").is_file()


def test_unprepared_prompt_without_repository_rules_still_contains_owner_question(tmp_path):
    prompt = dialogue.build_prompt("Explain the current state.", tmp_path)
    assert "Explain the current state." in prompt
    assert "Repository rules:" not in prompt
