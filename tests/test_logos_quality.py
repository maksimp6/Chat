"""End-to-end native shaping controls with only provider/GitHub transports replaced."""

import hashlib
import json
import subprocess

import pytest

from scripts import logos_dialogue as dialogue
from scripts import logos_quality as quality
from tests.test_logos_dialogue import (  # noqa: F401 -- shared real-runtime fixtures
    native_response,
    native_transport,
    no_network,
    prepared_context,
    repo,
)
from tool_providers.registry import registry
from core.config import get_model_info, upstream_model_id
from yandex_client_modules.errors import YandexClientError


def test_complete_quality_profile_reaches_actual_provider_payload(repo, native_transport):
    packet, knowledge = prepared_context(repo)
    client, calls = native_transport(native_response(1))
    result = dialogue.run_dialogue(
        "Проверь собственные возможности по материалам дела.",
        repo_root=repo,
        context_packet=packet,
        knowledge_receipt=knowledge,
        client_factory=lambda: client,
    )
    assert result["status"] == "completed"
    payload = calls[1][1]
    assert payload["model"].endswith("/deepseek-v4.1-flash/latest")
    assert payload["reasoning"] == {"effort": "high"}
    assert payload["max_output_tokens"] == 393216
    assert payload["truncation"] == "disabled"
    assert payload["background"] is True and payload["store"] is True
    hosted = {tool["type"]: tool for tool in payload["tools"] if tool["type"] != "function"}
    assert set(hosted) == {"web_search", "file_search", "code_interpreter"}
    assert hosted["file_search"]["vector_store_ids"] == knowledge["vector_store_ids"]
    assert hosted["web_search"]["search_context_size"] == "high"
    assert hosted["file_search"]["max_num_results"] == 50
    assert hosted["code_interpreter"]["container"] == {"type": "auto"}
    names = {tool["name"] for tool in payload["tools"] if tool["type"] == "function"}
    assert names == {tool["name"] for tool in registry.get_definitions()}
    actual_input = payload["input"][0]["content"]
    for source in [packet["owner_context"], *packet["sources"]]:
        assert json.dumps(source["text"], ensure_ascii=False) in actual_input
    receipt = result["capability_receipt"]
    assert receipt["initial_input"]["sha256"] == hashlib.sha256(actual_input.encode()).hexdigest()
    assert receipt["local_round_cap"] is None
    assert receipt["provider_max_output_tokens"] == "UNKNOWN"
    assert receipt["provider_max_hosted_calls"] == "UNKNOWN"
    assert receipt["provider_confirmed_cost"] is None
    assert result["generation_requests"] == 1


@pytest.mark.parametrize("damage", ["missing_context", "missing_rag", "stale_rag", "failed_rag"])
def test_missing_or_stale_prerequisites_never_reach_a_provider(repo, damage):
    packet, knowledge = prepared_context(repo)
    if damage == "missing_context":
        packet = None
    elif damage == "missing_rag":
        knowledge = None
    elif damage == "stale_rag":
        knowledge["context_sha256"] = "0" * 64
    else:
        knowledge["status"] = "failed"
    result = dialogue.run_dialogue(
        "question",
        repo_root=repo,
        context_packet=packet,
        knowledge_receipt=knowledge,
        client_factory=lambda: pytest.fail("Incomplete conditions reached the provider"),
    )
    assert result["status"] == "failed"
    assert result["generation_requests"] == 0


def test_large_initial_context_is_sent_whole_and_never_uses_hidden_truncation(
    repo, native_transport
):
    packet, knowledge = prepared_context(repo)
    # Add to owner material through the public builder, keeping integrity hashes real.
    owner_text = "BEGIN_OWNER_DIALOGUE\n" + "Полный контекст. " * 18000 + "\nEND_OWNER_DIALOGUE"
    old = packet["owner_context"]["text"]
    question = owner_text + "\n" + old
    client, calls = native_transport(native_response(1))
    result = dialogue.run_dialogue(
        question,
        repo_root=repo,
        context_packet=packet,
        knowledge_receipt=knowledge,
        client_factory=lambda: client,
    )
    assert result["status"] == "completed"
    full_input = calls[1][1]["input"][0]["content"]
    assert question in full_input and len(full_input.encode()) > 65536
    assert "END_OWNER_DIALOGUE" in full_input
    assert calls[1][1]["truncation"] == "disabled"


def test_strong_model_is_registered_instead_of_using_alice_pricing_fallback():
    assert upstream_model_id(quality.MODEL) == quality.MODEL
    info = get_model_info(quality.MODEL)
    assert info["name"] == "DeepSeek V4.1 Flash"
    assert info["context_window"] == 1048576
    assert info["output"] == 0.50


def test_checkpoint_keeps_known_response_id_before_a_poll_timeout(repo, native_transport, tmp_path):
    packet, knowledge = prepared_context(repo)
    queued = {"id": "known-response-before-timeout", "status": "queued", "output": []}
    client, calls = native_transport(queued)

    def timeout(*args, **kwargs):
        saved = json.loads(checkpoint.read_text())
        assert saved["response_ids"] == [queued["id"]]
        assert saved["status"] == "running"
        raise YandexClientError("Provider did not finish")

    checkpoint = tmp_path / "answer.json"
    client._wait = timeout
    result = dialogue.run_dialogue(
        "question",
        repo_root=repo,
        context_packet=packet,
        knowledge_receipt=knowledge,
        checkpoint_file=checkpoint,
        client_factory=lambda: client,
    )
    assert result["status"] == "failed" and result["response_ids"] == [queued["id"]]
    assert len(calls) == 2
    assert result["capability_receipt"]["requested"]["reasoning"] == {"effort": "high"}


def test_hosted_evidence_is_exported_without_inventing_effective_settings(repo, native_transport):
    packet, knowledge = prepared_context(repo)
    response = native_response(1)
    response["max_output_tokens"] = 393216
    response["output"][:0] = [
        {"type": kind, "id": f"hosted-{index}", "status": "completed"}
        for index, kind in enumerate(
            ("web_search_call", "file_search_call", "code_interpreter_call")
        )
    ]
    client, _ = native_transport(response)
    result = dialogue.run_dialogue(
        "question",
        repo_root=repo,
        context_packet=packet,
        knowledge_receipt=knowledge,
        client_factory=lambda: client,
    )
    assert result["status"] == "completed" and len(result["hosted_tool_calls"]) == 3
    receipt = result["capability_receipt"]
    assert receipt["provider_echo"]["max_output_tokens"] == 393216
    assert receipt["provider_max_output_tokens"] == "UNKNOWN"


@pytest.mark.parametrize("commit_change", [False, True])
def test_persisted_context_is_rejected_after_checkout_or_guidance_changes(repo, commit_change):
    packet, knowledge = prepared_context(repo)
    (repo / "AGENTS.md").write_text("Changed rules after context capture")
    if commit_change:
        subprocess.run(["git", "add", "AGENTS.md"], cwd=repo, check=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.invalid",
                "commit",
                "-qm",
                "changed checkout",
            ],
            cwd=repo,
            check=True,
        )
    result = dialogue.run_dialogue(
        "question",
        repo_root=repo,
        context_packet=packet,
        knowledge_receipt=knowledge,
        client_factory=lambda: pytest.fail("Stale context reached provider"),
    )
    assert result["status"] == "failed" and result["generation_requests"] == 0


def test_cli_refuses_question_without_initial_context_before_generation(tmp_path, monkeypatch):
    source, output = tmp_path / "question", tmp_path / "result.json"
    source.write_text("question")
    monkeypatch.setattr(
        dialogue, "_native_client", lambda: pytest.fail("Missing context started generation")
    )
    assert dialogue.main(["--question-file", str(source), "--out", str(output)]) == 1
    assert json.loads(output.read_text())["generation_requests"] == 0


def test_validation_evidence_omits_arbitrary_error_text_and_keys():
    private = "ghp_" + "x" * 40
    fields = dialogue._safe_validation_fields(
        {
            "code": "invalid_request_error",
            "param": "max_output_tokens",
            "type": private,
            "message": f"max_output_tokens must be less than or equal to 32768. {private}",
        }
    )
    assert fields == {
        "code": "invalid_request_error",
        "param": "max_output_tokens",
        "constraint_hints": {"max_output_tokens": 32768},
    }
    assert private not in json.dumps(fields)
