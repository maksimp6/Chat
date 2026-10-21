"""Run one owner-requested dialogue through Logos's native runtime in CI."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable
import uuid

from agent_runtime.responses_tool_loop import extract_function_calls
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
from yandex_client_modules.parsers import extract_reasoning_and_text

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL = "logos-runtime-flash"
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


def build_prompt(question: str, repo_root: Path) -> str:
    rules = (repo_root / "AGENTS.md").read_text(encoding="utf-8")
    return (
        "You are Logos, running in your own repository in a GitHub Actions runner.\n"
        "Answer the owner's question in their language. Your native filesystem tools "
        "are available to inspect the repository or run a build when useful. "
        "Describe only work you actually performed. This is one dialogue turn, "
        "not a request to start recurring or background work.\n\n"
        f"Repository rules:\n{rules}\n\nOwner question:\n{question}"
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
        "model": DEFAULT_MODEL,
        "reply": "",
        "pending_tools": [],
        "response_ids": [],
        "generation_requests": 0,
        "usage": {},
        "cost": {"status": "unknown", "provider_confirmed": False},
    }


def _ask(client, prompt: str, trace, result: dict[str, Any]) -> None:
    conversation_id = client.create_conversation(trace)["id"]
    result["conversation_id"] = conversation_id
    response = client.ask_with_mcp(
        message=prompt,
        model_key=DEFAULT_MODEL,
        conversation_id=conversation_id,
        params={
            "active_tool_categories": ["filesystem"],
            "tools_config": {
                "web_search": {"enabled": False},
                "file_search": {"enabled": False},
                "code_interpreter": {"enabled": False},
            },
            "background": False,
            "store": True,
        },
        trace=trace,
    )
    _, reply = extract_reasoning_and_text(response)
    result["reply"] = (reply or "").strip()
    if response.get("id"):
        result["response_ids"].append(response["id"])
    pending = extract_function_calls(response)
    result["pending_tools"] = sorted({call.get("name") or "unknown" for call in pending})
    result["provider_status"] = response.get("status") or "unknown"
    if pending:
        result["status"] = "needs_approval"
    elif response.get("status") not in (None, "completed"):
        result.update(status="incomplete", error="IncompleteResponse")
    elif result["reply"]:
        result["status"] = "completed"
    else:
        raise RuntimeError("Provider returned no answer")


def _finish(invocation, trace, result: dict[str, Any]) -> None:
    finalized = trace.finalize()
    persist_invocation_trace(invocation.invocation_id, finalized)
    result["generation_requests"] = len(finalized.get("api_requests", []))
    responses = finalized.get("responses", [])
    response_ids = [item["response_id"] for item in responses if item.get("response_id")]
    result["response_ids"] = list(dict.fromkeys(response_ids + result["response_ids"]))
    if any((item.get("raw") or {}).get("usage") for item in responses):
        billing = finalized.get("billing") or {}
        result["usage"] = {
            key: billing[key]
            for key in ("input_tokens", "output_tokens", "total_tokens")
            if key in billing
        }
    if result["status"] in {"failed", "incomplete"}:
        fail_invocation(invocation.invocation_id, error={"type": result["error"]})
    else:
        finish_invocation(invocation.invocation_id, result=result)


def run_dialogue(
    question: str,
    *,
    repo_root: Path = ROOT,
    client_factory: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    result = _new_result()
    invocation = None
    trace = None
    try:
        if not question.strip():
            raise ValueError("The question must not be empty")
        prompt = build_prompt(question, Path(repo_root))
        import app  # noqa: F401 -- initialize the native database schema and tools

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
        _ask(client, prompt, trace, result)
    except RUNTIME_ERRORS as exc:
        result.update(status="failed", error=type(exc).__name__)
        if trace is not None:
            trace.record_error("logos_dialogue", "Dialogue failed", error_type=type(exc).__name__)
    if trace is not None and invocation is not None:
        try:
            _finish(invocation, trace, result)
        except RUNTIME_ERRORS as exc:
            result.update(status="failed", error=type(exc).__name__)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--question-file", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = run_dialogue(args.question_file.read_text(encoding="utf-8"))
    except RUNTIME_ERRORS as exc:
        result = _new_result()
        result["error"] = type(exc).__name__
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {"status": result["status"], "generation_requests": result["generation_requests"]}
        )
    )
    return 0 if result["status"] == "completed" else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
