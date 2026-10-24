"""Run one owner-requested dialogue through Logos's native runtime in CI."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Callable
import uuid

from agent_runtime.provider_receipt import (
    assistant_text,
    capture_provider_diagnostics as _provider_diagnostics,
    provider_echo,
    provider_token as _provider_token,
)
from agent_runtime.responses_tool_loop import extract_function_calls
from agent_runtime.logos_launch_hold import (
    NativeLogosLaunchBlocked,
    enforce_native_launch_hold,
    hold_receipt,
)
from invocation.manager import (
    create_invocation,
    fail_invocation,
    finish_invocation,
    persist_invocation_trace,
    start_invocation,
)
from invocation.trace import create_invocation_trace
from storage.memory_db import DatabaseError
from yandex_client_modules.errors import YandexClientError
from scripts.logos_quality import MODEL, OWNER_DIRECTIVE, capability_receipt, dialogue_params

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL = MODEL
DIALOGUE_INSTRUCTIONS = (
    "Ты — Логос, помощник владельца репозитория. Отвечай по существу на языке вопроса. "
    "На общие вопросы отвечай на основе своих знаний, обозначая неопределённость. "
    "Инструменты используй по необходимости: доступ к репозиторию не ограничивает темы "
    "разговора и не требует подтверждать общие знания файлами проекта. "
    "Правила репозитория относятся к работе с кодом и инструментами. "
    "Отличай исторические сведения от религиозных представлений и других интерпретаций.\n"
    + OWNER_DIRECTIVE
)
RUNTIME_ERRORS = (
    OSError,
    RuntimeError,
    ValueError,
    TypeError,
    LookupError,
    ImportError,
    DatabaseError,
    YandexClientError,
)


def build_prompt(question: str, repo_root: Path, context_packet: dict | None = None) -> str:
    if context_packet is not None:
        from scripts.logos_context import render_context

        return (
            "You are Logos working in your own GitHub Actions checkout.\n"
            + render_context(context_packet)
            + "\n\nCurrent owner question:\n"
            + question
        )
    rules_path = repo_root / "AGENTS.md"
    rules = rules_path.read_text(encoding="utf-8") if rules_path.exists() else ""
    return (
        "You are Logos, running in your own repository in a GitHub Actions runner.\n"
        "Answer the owner's question in their language. Your native filesystem tools "
        "are available to inspect the repository or run a build when useful. "
        "Describe only work you actually performed. This is one dialogue turn, "
        "not a request to start recurring or background work.\n\n"
        + (f"Repository rules:\n{rules}\n\n" if rules else "")
        + f"Owner question:\n{question}"
    )


def _native_client():
    from agent_runtime.issue_runner import seed_provider_credential
    from chat.mcp_routes import LogosClient
    from core.config import Config

    seed_provider_credential()
    return LogosClient(Config)


def _new_result() -> dict[str, Any]:
    return {
        "status": "failed",
        "provider_status": "unknown",
        "model": DEFAULT_MODEL,
        "reply": "",
        "pending_tools": [],
        "response_ids": [],
        "tool_calls": [],
        "hosted_tool_calls": [],
        "generation_requests": 0,
        "usage": {},
        "cost": {"status": "unknown", "provider_confirmed": False},
    }


def _provider_echo(result: dict[str, Any], response: dict[str, Any]) -> None:
    echo = provider_echo(response)
    if echo:
        result["provider_echo"] = echo
        if "capability_receipt" in result:
            result["capability_receipt"]["provider_echo"] = echo


def _ask(client, prompt: str, trace, result: dict[str, Any], params: dict) -> None:
    enforce_native_launch_hold()
    conversation_id = client.create_conversation(trace)["id"]
    result["conversation_id"] = conversation_id
    response = client.ask_with_mcp(
        message=prompt,
        model_key=DEFAULT_MODEL,
        conversation_id=conversation_id,
        params=params,
        trace=trace,
    )
    _stored_reply(result, response)
    if response.get("id") and response["id"] not in result["response_ids"]:
        result["response_ids"].append(response["id"])
    pending = extract_function_calls(response)
    result["pending_tools"] = sorted({call.get("name") or "unknown" for call in pending})
    result["provider_status"] = "unknown"
    _provider_diagnostics(result, response)
    if result["provider_error_present"]:
        result.update(status="failed", error="ProviderResponseError")
    elif pending:
        result["status"] = "needs_approval"
    elif response.get("status") not in (None, "completed"):
        result.update(status="incomplete", error="IncompleteResponse")
    elif result["reply"].strip():
        result["status"] = "completed"
    else:
        raise RuntimeError("Provider returned no answer")


def _checkpoint_response(result: dict[str, Any], response: dict, path: Path | None) -> None:
    """Persist known response identity before a long poll, never retry a generation."""
    response_id = response.get("id")
    if isinstance(response_id, str) and response_id not in result["response_ids"]:
        result["response_ids"].append(response_id)
    _provider_diagnostics(result, response)
    _stored_reply(result, response)
    result["checkpoint"] = {"state": "response_received", "reply_verified": False}
    if path is not None:
        snapshot = {**result, "status": "running"}
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)


def _tool_evidence(entry: dict[str, Any]) -> dict[str, Any]:
    output = entry.get("result") or {}
    data = output.get("data")
    data = data if isinstance(data, dict) else {}
    evidence = {
        "name": entry.get("name"),
        "call_id": entry.get("call_id"),
        "success": output.get("success"),
    }
    if (output.get("metadata") or {}).get("phase") == "approval_required":
        evidence["approval_required"] = True
    if "returncode" in data:
        evidence["returncode"] = data["returncode"]
    arguments = entry.get("arguments") or {}
    values = {"command_sha256": arguments.get("command") or arguments.get("cmd")}
    if entry.get("name") == "run_command":
        values["stdout_sha256"] = data.get("stdout")
    for key, value in values.items():
        # The native trace may redact or truncate strings. Such a value cannot
        # establish a digest of the original tool input/output.
        if isinstance(value, str) and not any(
            marker in value for marker in ("<redacted", "<truncated")
        ):
            evidence[key] = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return evidence


def _response_evidence(responses: list[dict], result: dict[str, Any]) -> None:
    for item in responses:
        raw = item.get("raw") or {}
        if not isinstance(raw, dict):
            continue
        for output in raw.get("output", []) or []:
            if isinstance(output, dict) and output.get("type") in {
                "web_search_call",
                "file_search_call",
                "code_interpreter_call",
            }:
                evidence = {key: output[key] for key in ("type", "id", "status") if key in output}
                if evidence not in result["hosted_tool_calls"]:
                    result["hosted_tool_calls"].append(evidence)
        _provider_echo(result, raw)


def _finish(invocation, trace, result: dict[str, Any]) -> None:
    finalized = trace.finalize()
    # Export independent runtime evidence before any durable write. A storage
    # failure must never erase completed provider/tool execution from the report.
    result["tool_calls"] = [_tool_evidence(entry) for entry in finalized.get("tool_calls", [])]
    result["approval_refused_tools"] = sorted(
        {item["name"] for item in result["tool_calls"] if item.get("approval_required")}
    )
    result["generation_requests"] = len(finalized.get("api_requests", []))
    result["api_failures"] = _api_failure_evidence(finalized.get("events", []))
    responses = finalized.get("responses", [])
    _response_evidence(responses, result)
    if responses and isinstance(responses[-1].get("raw"), dict):
        _provider_diagnostics(result, responses[-1]["raw"])
        if result["provider_error_present"] and result["status"] != "retrieved":
            result.update(status="failed", error="ProviderResponseError")
    response_ids = [item["response_id"] for item in responses if item.get("response_id")]
    result["response_ids"] = list(dict.fromkeys(response_ids + result["response_ids"]))
    if any((item.get("raw") or {}).get("usage") for item in responses):
        billing = finalized.get("billing") or {}
        result["usage"] = {
            key: billing[key]
            for key in ("input_tokens", "output_tokens", "total_tokens")
            if key in billing
        }
    trace_bytes = len(json.dumps(finalized, ensure_ascii=False).encode("utf-8"))
    stage = "trace"
    result["persistence"] = {
        "status": "writing",
        "engine": "native_kristall",
        "trace_bytes": trace_bytes,
    }
    try:
        if not persist_invocation_trace(invocation.invocation_id, finalized):
            raise RuntimeError("invocation record missing")
        result["persistence"]["status"] = "stored"
        stage = "completion"
        if result["status"] == "completed" and result["reply"]:
            result["checkpoint"] = {
                "state": "completed",
                "reply_verified": True,
                "verification_scope": "provider_reply_and_invocation_persistence",
            }
        if result["status"] in {"failed", "incomplete"}:
            saved = fail_invocation(invocation.invocation_id, error={"type": result["error"]})
        else:
            saved = finish_invocation(invocation.invocation_id, result=result)
        if not saved:
            raise RuntimeError("invocation completion unavailable")
    except RUNTIME_ERRORS as exc:
        reasons = {
            "value text exceeds size limit": "value_text_limit",
            "value structure exceeds limit": "value_structure_limit",
            "journal frame exceeds size limit": "journal_frame_limit",
            "commit failed; recovery required": "journal_commit_recovery_required",
            "database is closed": "store_closed",
            "invocation record missing": "invocation_missing",
            "invocation completion unavailable": "completion_unavailable",
        }
        result["persistence"].update(
            status="failed",
            stage=stage,
            error_type=type(exc).__name__,
            reason=reasons.get(str(exc), "unclassified"),
        )
        if "checkpoint" in result:
            result["checkpoint"].update(state="finalization_failed", reply_verified=False)
        raise


def _api_failure_evidence(events: list[dict]) -> list[dict]:
    """Export machine validation evidence without arbitrary provider error text."""
    evidence = []
    for event in events:
        if event.get("type") == "api_request_error":
            payload = event.get("payload") or {}
            detail = payload.get("detail") or {}
            error = detail.get("error", detail) if isinstance(detail, dict) else {}
            record = {"status_code": payload.get("status_code")}
            if isinstance(error, dict):
                record.update(_safe_validation_fields(error))
            evidence.append(record)
    return evidence


def _safe_validation_fields(error: dict) -> dict:
    fields = {
        key: token
        for key in ("code", "type", "param")
        if (token := _provider_token(error.get(key))) is not None
    }
    message = error.get("message")
    if isinstance(message, str):
        hints = re.findall(
            r"(max_output_tokens|max_tool_calls)\b[^\n]{0,60}?"
            r"(?:at most|less than or equal to|<=|maximum(?: is|:)?)[^\d\n]{0,10}(\d{1,9})",
            message,
            re.IGNORECASE,
        )
        if hints:
            fields["constraint_hints"] = {key.lower(): int(value) for key, value in hints}
    return fields


def run_dialogue(
    question: str,
    *,
    repo_root: Path = ROOT,
    client_factory: Callable[[], Any] | None = None,
    context_packet: dict | None = None,
    knowledge_receipt: dict | None = None,
    checkpoint_file: Path | None = None,
) -> dict[str, Any]:
    result = _new_result()
    invocation = None
    trace = None
    try:
        enforce_native_launch_hold()
        if not question.strip():
            raise ValueError("The question must not be empty")
        if not isinstance(context_packet, dict) or not isinstance(knowledge_receipt, dict):
            raise ValueError("Native dialogue requires full context and prepared RAG")
        from scripts.logos_context import verify_checkout

        verify_checkout(context_packet, Path(repo_root))
        prompt = build_prompt(question, Path(repo_root), context_packet)
        import app  # noqa: F401 -- initialize the native database schema and tools

        from tool_providers.registry import registry
        from scripts.logos_project_tools import register_project_reader

        # The read token belongs to the scoped read transport, never to shell env,
        # function arguments, instructions, or exported receipts.
        project_token = os.environ.pop("GITHUB_TOKEN", "")
        register_project_reader(project_token, context_packet["repository"])

        if knowledge_receipt.get("status") != "indexed":
            raise ValueError("Knowledge preparation is incomplete")
        if knowledge_receipt.get("context_sha256") != context_packet.get("packet_sha256"):
            raise ValueError("Knowledge and initial context must be the same snapshot")
        vector_ids = knowledge_receipt["vector_store_ids"]
        params = dialogue_params(vector_ids, list(registry.get_available_categories()))
        params["instructions"] = DIALOGUE_INSTRUCTIONS
        result["capability_receipt"] = capability_receipt(params)
        result["capability_receipt"]["initial_input"] = {
            "sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "bytes": len(prompt.encode("utf-8")),
            "characters": len(prompt),
            "context_manifest": (context_packet or {}).get("manifest"),
            "context_status": "complete" if context_packet else "local_rules_only",
        }
        result["capability_receipt"]["knowledge"] = knowledge_receipt or {"status": "not_prepared"}
        params["on_provider_response"] = lambda response: _checkpoint_response(
            result, response, checkpoint_file
        )

        session_id = "github-dialogue-" + str(uuid.uuid4())
        invocation = create_invocation(
            session_id, session_id, metadata={"source": "logos_dialogue"}
        )
        trace = create_invocation_trace(invocation)
        start_invocation(invocation.invocation_id)
        result.update(
            invocation_id=invocation.invocation_id,
            trace_id=invocation.trace_id,
            session_id=session_id,
        )
        client = (client_factory or _native_client)()
        _ask(client, prompt, trace, result, params)
    except RUNTIME_ERRORS as exc:
        result.update(status="failed", error=type(exc).__name__)
        if isinstance(exc, NativeLogosLaunchBlocked):
            result["launch_hold"] = hold_receipt()
        if trace is not None:
            trace.record_error("logos_dialogue", "Dialogue failed", error_type=type(exc).__name__)
    if trace is not None and invocation is not None:
        try:
            _finish(invocation, trace, result)
        except RUNTIME_ERRORS as exc:
            result.update(status="failed", error=type(exc).__name__)
    return result


def _stored_reply(result: dict[str, Any], response: dict[str, Any]) -> None:
    """Export explicit assistant text, never reasoning or arbitrary diagnostic text."""
    result.update(reply="", reply_state="unknown")
    result.pop("reply_chars", None)
    result.pop("reply_sha256", None)
    reply, state = assistant_text(response)
    if state != "unknown":
        result.update(
            reply=reply,
            reply_state="available" if reply else "empty",
            reply_chars=len(reply),
            reply_sha256=hashlib.sha256(reply.encode("utf-8")).hexdigest(),
        )
        receipt = {
            "response_id": response.get("id"),
            **{key: result[key] for key in ("reply", "reply_state", "reply_chars", "reply_sha256")},
        }
        replies = result.setdefault("assistant_replies", [])
        if receipt not in replies:
            replies.append(receipt)


def inspect_response(
    response_id: str, *, client_factory: Callable[[], Any] | None = None
) -> dict[str, Any]:
    """Read one stored provider response without starting or continuing a generation."""
    result = _new_result()
    result["reply_state"] = "unknown"
    result["retrieval_requests"] = 0
    invocation = None
    trace = None
    try:
        if not isinstance(response_id, str):
            raise ValueError("The response ID must be a UUID string")
        response_id = str(uuid.UUID(response_id))
        result["response_ids"] = [response_id]
        import app  # noqa: F401 -- initialize the native credential and invocation store
        from yandex_client_modules.request_mixin import _resolve_global_provider_credential

        session_id = "github-response-" + str(uuid.uuid4())
        invocation = create_invocation(
            session_id, session_id, metadata={"source": "logos_response_inspection"}
        )
        trace = create_invocation_trace(invocation)
        start_invocation(invocation.invocation_id)
        result.update(
            invocation_id=invocation.invocation_id,
            trace_id=invocation.trace_id,
            session_id=session_id,
        )
        client = (client_factory or _native_client)()
        _resolve_global_provider_credential(client, trace)
        result["retrieval_requests"] = 1
        response = client.session.get(client.responses_url + "/" + response_id, timeout=15)
        result["http_status"] = response.status_code
        response.raise_for_status()
        data = response.json()
        stored_id = data.get("id") if isinstance(data, dict) else None
        if not isinstance(stored_id, str) or str(uuid.UUID(stored_id)) != response_id:
            raise ValueError("Stored response ID does not match the requested response")
        _stored_reply(result, data)
        trace.add_response(data, step_index=1, kind="retrieved_response")
        _provider_diagnostics(result, data)
        result["status"] = "retrieved"
    except RUNTIME_ERRORS as exc:
        result.update(status="failed", error=type(exc).__name__)
        if trace is not None:
            trace.record_error(
                "logos_response_inspection",
                "Response inspection failed",
                error_type=type(exc).__name__,
            )
    if trace is not None and invocation is not None:
        try:
            _finish(invocation, trace, result)
        except RUNTIME_ERRORS as exc:
            result.update(status="failed", error=type(exc).__name__)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--question-file", type=Path)
    source.add_argument("--response-id")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--context-file", type=Path)
    parser.add_argument("--knowledge-file", type=Path)
    args = parser.parse_args(argv)
    from agent_runtime.generation_gate import GENERATION_BLOCKED, generation_allowed

    try:
        if args.response_id is not None:
            result = inspect_response(args.response_id)
        elif not generation_allowed():
            # Reading a stored response stays available; a new generation needs owner permission.
            result = _new_result()
            result.update(status="blocked", error=GENERATION_BLOCKED)
        else:
            if args.context_file is None or args.knowledge_file is None:
                raise ValueError("Native dialogue requires full context and prepared RAG")
            from scripts.logos_context import load_context

            result = run_dialogue(
                args.question_file.read_text(encoding="utf-8"),
                context_packet=load_context(args.context_file),
                knowledge_receipt=json.loads(args.knowledge_file.read_text(encoding="utf-8")),
                checkpoint_file=args.out,
            )
    except RUNTIME_ERRORS as exc:
        result = _new_result()
        result["error"] = type(exc).__name__
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("LOGOS_RESULT=" + json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] in {"completed", "retrieved"} else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
