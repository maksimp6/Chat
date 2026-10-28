"""Helpers for continuing Yandex Responses API requests after tool calls."""

from __future__ import annotations

import json
from typing import Any, Callable, Iterable


# WIKIFUNCTIONS-CANDIDATE WF-12: https://www.wikifunctions.org/wiki/Z872 + Z873 + Z18755.
# Заменить ядро сбора calls на filter/map/concat; адаптер обходит только output и message.content,
# сохраняет порядок и исходные dict, не удаляет повторные call_id и не исполняет вызовы.
def extract_function_calls(response: Any) -> list[dict[str, Any]]:
    """Return function calls from a Responses API output payload."""
    calls: list[dict[str, Any]] = []
    if not isinstance(response, dict):
        return calls
    output = response.get("output")
    if not isinstance(output, list):
        return calls
    for item in output:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "function_call":
            calls.append(item)
        elif item.get("type") == "message":
            content = item.get("content")
            if not isinstance(content, list):
                continue
            calls.extend(
                part
                for part in content
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


# WIKIFUNCTIONS-CANDIDATE WF-13: https://www.wikifunctions.org/wiki/Z14779.
# Заменить попарную сборку на map двух списков одинаковой длины; адаптер материализует
# results один раз, сохраняет прежнюю ошибку размера до сборки и callback make_function_call_output.
def build_continuation_input(
    response: dict[str, Any], results: Iterable[Any]
) -> list[dict[str, Any]]:
    """Pair tool calls with execution results in response order."""
    calls = extract_function_calls(response)
    values = list(results)
    if len(calls) != len(values):
        raise ValueError(
            f"tool call/result count mismatch: {len(calls)} != {len(values)}"
        )
    return [
        make_function_call_output(call, result) for call, result in zip(calls, values)
    ]


def run_tool_loop(
    initial_response: dict[str, Any],
    execute_tool: Callable[[dict[str, Any]], Any],
    continue_request: Callable[[list[dict[str, Any]], dict[str, Any]], dict[str, Any]],
    *,
    max_rounds: int | None = 16,
    should_pause: Callable[[list[dict[str, Any]]], bool] | None = None,
) -> dict[str, Any]:
    """Execute function calls and continue until a response has no tool calls.

    ``continue_request`` receives the complete previous ``output`` plus the
    generated ``function_call_output`` items. This preserves reasoning and
    other response items required by the Responses API protocol.

    ``max_rounds=None`` leaves the caller's job/task lifetime as the boundary;
    it does not impose an arbitrary tool-round ceiling. ``should_pause`` is
    checked before every batch, including continuations, so approvals never
    become implicit after the initial request.
    """
    if max_rounds is not None and (
        isinstance(max_rounds, bool)
        or not isinstance(max_rounds, int)
        or max_rounds < 0
    ):
        raise ValueError("max_rounds must be a non-negative integer or None")
    response = initial_response
    rounds = 0
    executed: set[str] = set()
    while max_rounds is None or rounds < max_rounds:
        if not isinstance(response, dict):
            raise ValueError("Responses API returned a non-object response")
        # A truncated call may contain only part of its arguments. Preserve the
        # provider failure for the caller; never execute its partial output.
        if response.get("error") is not None or response.get("status") != "completed":
            return response
        calls = extract_function_calls(response)
        if not calls:
            return response
        if should_pause is not None and should_pause(calls):
            return response
        # Validate the whole batch before any side effect. In particular, a
        # missing correlation id used to fail only AFTER executing the tool.
        batch_ids = set()
        for call in calls:
            call_id = call.get("call_id") or call.get("id")
            if not isinstance(call_id, str) or not call_id.strip():
                raise ValueError("function_call is missing a valid call_id/id")
            if call_id in batch_ids:
                raise ValueError("duplicate function_call id in one response")
            batch_ids.add(call_id)
            if call.get("status") not in (None, "completed"):
                raise ValueError("function_call is not completed")
            if call_id in executed:
                raise ValueError("function_call id already executed")
        tool_outputs = []
        for call in calls:
            call_id = call.get("call_id") or call.get("id")
            result = execute_tool(call)
            tool_outputs.append(make_function_call_output(call, result))
            executed.add(call_id)
        continuation_input = list(response.get("output", []) or []) + tool_outputs
        response = continue_request(continuation_input, response)
        rounds += 1
    # The last continuation can itself request a gated call. That call must reach the approval
    # check like any other batch, not fail the loop.
    if not isinstance(response, dict):
        raise ValueError("Responses API returned a non-object response")
    calls = extract_function_calls(response)
    if response.get("error") is not None or response.get("status") != "completed":
        return response
    if not calls:
        return response
    if should_pause is not None and should_pause(calls):
        return response
    raise RuntimeError("Responses API tool loop exceeded max_rounds")
