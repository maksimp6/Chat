"""Helpers for continuing Yandex Responses API requests after tool calls."""

from __future__ import annotations

import json
from typing import Any, Callable, Iterable


def extract_function_calls(response: dict[str, Any]) -> list[dict[str, Any]]:
    """Return function calls from a Responses API output payload."""
    calls: list[dict[str, Any]] = []
    for item in response.get("output", []) or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "function_call":
            calls.append(item)
        elif item.get("type") == "message":
            calls.extend(
                part
                for part in item.get("content", []) or []
                if isinstance(part, dict) and part.get("type") == "function_call"
            )
    return calls


def make_function_call_output(call: dict[str, Any], result: Any) -> dict[str, Any]:
    """Build the protocol item required for a tool-result continuation."""
    call_id = call.get("call_id") or call.get("id")
    if not call_id:
        raise ValueError("function_call is missing call_id/id")
    if isinstance(result, (dict, list)):
        output = json.dumps(result, ensure_ascii=False)
    else:
        output = "" if result is None else str(result)
    return {"type": "function_call_output", "call_id": call_id, "output": output}


def build_continuation_input(
    response: dict[str, Any], results: Iterable[Any]
) -> list[dict[str, Any]]:
    """Pair tool calls with execution results in response order."""
    calls = extract_function_calls(response)
    values = list(results)
    if len(calls) != len(values):
        raise ValueError(f"tool call/result count mismatch: {len(calls)} != {len(values)}")
    return [make_function_call_output(call, result) for call, result in zip(calls, values)]


def run_tool_loop(
    initial_response: dict[str, Any],
    execute_tool: Callable[[dict[str, Any]], Any],
    continue_request: Callable[[list[dict[str, Any]], dict[str, Any]], dict[str, Any]],
    *,
    max_rounds: int = 16,
) -> dict[str, Any]:
    """Execute function calls and continue until a response has no tool calls.

    ``continue_request`` receives the complete previous ``output`` plus the
    generated ``function_call_output`` items. This preserves reasoning and
    other response items required by the Responses API protocol.
    """
    response = initial_response
    for _ in range(max_rounds):
        calls = extract_function_calls(response)
        if not calls:
            return response
        results = [execute_tool(call) for call in calls]
        tool_outputs = build_continuation_input(response, results)
        continuation_input = list(response.get("output", []) or []) + tool_outputs
        response = continue_request(continuation_input, response)
    raise RuntimeError("Responses API tool loop exceeded max_rounds")
