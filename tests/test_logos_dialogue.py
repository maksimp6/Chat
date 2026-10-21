"""Offline checks of the native Logos dialogue, transport, and persisted trace."""

import json
from pathlib import Path
import subprocess

import pytest
import requests

from chat.mcp_routes import LogosClient
from core.config import Config
from scripts import logos_dialogue as dialogue
from tool_providers import filesystem


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def reject(*args, **kwargs):
        pytest.fail("The dialogue tests must never use a live provider")

    monkeypatch.setattr(requests.Session, "request", reject)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "AGENTS.md").write_text("Use the existing runtime and report actual evidence.\n")
    (root / "README.md").write_text("Logos repository context.\n")
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


def test_native_dialogue_reads_repository_and_preserves_answer_usage_and_trace(repo, monkeypatch):
    from agent_runtime.issue_runner import seed_provider_credential
    from invocation.manager import get_invocation

    monkeypatch.setenv("ALICE_PROVIDER_CREDENTIAL_KEY", "offline-credential-encryption-key")
    monkeypatch.setenv("YANDEX_API_KEY", "offline-yandex-key")
    monkeypatch.setenv("YANDEX_PROJECT_ID", "offline-project")
    seed_provider_credential()
    client = LogosClient(Config)
    calls = []
    provider_responses = iter(
        [
            {"id": "00000000-0000-4000-8000-000000000001"},
            native_response(1, read=True),
            native_response(2),
        ]
    )

    class Response:
        status_code = 200
        url = "https://ai.api.cloud.yandex.net/v1/responses"

        def __init__(self, data):
            self.data = data

        def json(self):
            return self.data

        def raise_for_status(self):
            pass

    def post(url, *, json, timeout):
        calls.append((url, json))
        return Response(next(provider_responses))

    monkeypatch.setattr(client.session, "post", post)
    result = dialogue.run_dialogue(
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
    invocation = get_invocation(result["invocation_id"])
    assert invocation["status"] == "completed"
    assert invocation["trace"]["trace_id"] == result["trace_id"]
    assert invocation["session_id"] == result["session_id"]
    assert "offline-yandex-key" not in json.dumps(result)
    assert calls[2][1]["input"][-1]["type"] == "function_call_output"
    assert "Logos repository context." in calls[2][1]["input"][-1]["output"]
    assert calls[1][1]["instructions"] == dialogue.DIALOGUE_INSTRUCTIONS
    assert calls[2][1]["instructions"] == dialogue.DIALOGUE_INSTRUCTIONS
    available = {tool.get("name") for tool in calls[1][1]["tools"]}
    assert {"read_file", "write_file", "run_command"} <= available


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

    result = dialogue.run_dialogue(
        "Вопрос пользователя", repo_root=repo, client_factory=lambda: client
    )

    assert result["status"] == "completed"
    request = client.calls[0]
    assert request["model_key"] == "logos-runtime-flash"
    assert "Вопрос пользователя" in request["message"]
    assert "Use the existing runtime and report actual evidence." in request["message"]
    assert request["params"]["active_tool_categories"] == ["filesystem"]
    assert "max_tool_calls" not in request["params"]
    assert "max_output_tokens" not in request["params"]


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

    result = dialogue.run_dialogue("question", repo_root=repo, client_factory=lambda: client)

    assert result["status"] == "needs_approval"
    assert result["pending_tools"] == ["protected_tool"]
    assert result["reply"] == ""


def test_failed_dialogue_sanitizes_provider_exception_and_persists_failure(repo):
    from invocation.manager import get_invocation
    from yandex_client_modules.errors import YandexClientError

    class FailedClient(OfflineClient):
        def create_conversation(self, trace):
            raise YandexClientError("private provider details must not escape")

    result = dialogue.run_dialogue("question", repo_root=repo, client_factory=FailedClient)

    assert result["status"] == "failed" and result["error"] == "YandexClientError"
    assert "private provider details" not in json.dumps(result)
    invocation = get_invocation(result["invocation_id"])
    assert invocation["status"] == "failed"
    assert "private provider details" not in json.dumps(invocation["trace"])


def test_default_client_initializes_existing_credential_and_native_client(repo, monkeypatch):
    from agent_runtime import issue_runner
    import chat.mcp_routes as mcp_routes

    monkeypatch.setenv("ALICE_PROVIDER_CREDENTIAL_KEY", "offline-key")
    seeded = []
    configurations = []
    monkeypatch.setattr(issue_runner, "seed_provider_credential", lambda: seeded.append(True))

    def native_client(config):
        configurations.append(config)
        return OfflineClient()

    monkeypatch.setattr(mcp_routes, "LogosClient", native_client)
    result = dialogue.run_dialogue("question", repo_root=repo)

    assert result["status"] == "completed"
    assert seeded == [True] and configurations == [Config]


def test_empty_question_rejected_before_client_creation(repo):
    result = dialogue.run_dialogue(
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

    result = dialogue.run_dialogue("question", repo_root=repo, client_factory=lambda: client)

    assert result["status"] == "incomplete" and result["error"] == "IncompleteResponse"
    assert result["reply"] == "Ответ Logos из тестового транспорта."
    assert result["provider_status"] == "incomplete"
    assert get_invocation(result["invocation_id"])["status"] == "failed"


def test_empty_answer_is_failed_and_missing_usage_stays_unknown(repo):
    client = OfflineClient({"id": "empty-response", "status": "completed", "output": []})

    result = dialogue.run_dialogue("question", repo_root=repo, client_factory=lambda: client)

    assert result["status"] == "failed" and result["reply"] == ""
    assert result["usage"] == {}
    assert result["cost"] == {"status": "unknown", "provider_confirmed": False}


def test_cli_writes_actual_result_without_approval_flag(tmp_path, monkeypatch, capsys):
    question = tmp_path / "question.txt"
    question.write_text("вопрос пользователя", encoding="utf-8")
    output = tmp_path / "answer.json"
    captured = []
    reply = 'ответ\n::error::literal text\r$(touch escaped) "quote"'

    def run(question_text):
        captured.append(question_text)
        return {"status": "completed", "reply": reply, "generation_requests": 1}

    monkeypatch.setattr(dialogue, "run_dialogue", run)

    assert dialogue.main(["--question-file", str(question), "--out", str(output)]) == 0
    assert captured == ["вопрос пользователя"]
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["reply"] == reply
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1 and lines[0].startswith("LOGOS_RESULT=")
    assert json.loads(lines[0].removeprefix("LOGOS_RESULT=")) == result


def test_cli_missing_input_fails_safely_and_writes_status(tmp_path, capsys):
    output = tmp_path / "answer.json"

    assert (
        dialogue.main(
            [
                "--question-file",
                str(tmp_path / "private-missing-input.txt"),
                "--out",
                str(output),
            ]
        )
        == 1
    )

    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["status"] == "failed"
    assert "private-missing-input" not in json.dumps(result) + capsys.readouterr().out


def test_runner_roots_resolve_to_repository_not_package():
    from agent_runtime import issue_runner

    assert dialogue.ROOT == issue_runner.ROOT == Path(__file__).resolve().parents[1]
    assert (dialogue.ROOT / "AGENTS.md").is_file()
