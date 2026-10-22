import pytest

from agent_runtime.responses_tool_loop import (
    build_continuation_input,
    extract_function_calls,
    make_function_call_output,
    run_tool_loop,
)


def test_extract_function_calls_from_response_output():
    response = {
        "output": [
            {"type": "reasoning", "summary": []},
            {
                "type": "function_call",
                "id": "item-1",
                "call_id": "call-1",
                "name": "git_log",
                "arguments": '{"limit": 10}',
            },
        ]
    }
    assert [c["name"] for c in extract_function_calls(response)] == ["git_log"]


def test_make_function_call_output_uses_call_id():
    call = {"type": "function_call", "id": "item-1", "call_id": "call-1"}
    assert make_function_call_output(call, {"commits": []}) == {
        "type": "function_call_output",
        "call_id": "call-1",
        "output": '{"commits": []}',
    }


def test_continuation_rejects_mismatched_results():
    with pytest.raises(ValueError, match="count mismatch"):
        build_continuation_input(
            {"output": [{"type": "function_call", "call_id": "call-1"}]},
            [],
        )


def test_run_tool_loop_executes_tool_and_returns_final_response():
    first = {
        "id": "response-1",
        "output": [{"type": "function_call", "call_id": "call-1", "name": "git_log"}],
    }
    final = {"id": "response-2", "output_text": "10 commits"}
    executed = []
    continuations = []

    def execute(call):
        executed.append(call["name"])
        return {"commits": ["abc"]}

    def continue_request(items, previous):
        continuations.append(items)
        return final

    assert run_tool_loop(first, execute, continue_request) == final
    assert executed == ["git_log"]
    assert len(continuations) == 1
    assert continuations[0][-1] == {
        "type": "function_call_output",
        "call_id": "call-1",
        "output": '{"commits": ["abc"]}',
    }


def test_terminal_response_on_last_allowed_continuation_is_returned():
    first = {"output": [{"type": "function_call", "call_id": "call-1"}]}
    final = {"id": "response-final", "output": [{"type": "message", "content": []}]}
    calls = []

    def continue_request(items, previous):
        calls.append(items)
        return final

    assert run_tool_loop(first, lambda call: "source", continue_request, max_rounds=1) == final
    assert len(calls) == 1


def test_pending_calls_after_last_round_are_not_executed():
    first = {"output": [{"type": "function_call", "call_id": "call-1"}]}
    next_response = {"output": [{"type": "function_call", "call_id": "call-2"}]}
    executed = []
    continued = []

    def continue_request(items, previous):
        continued.append(items)
        return next_response

    with pytest.raises(RuntimeError, match="max_rounds"):
        run_tool_loop(
            first, lambda call: executed.append(call["call_id"]), continue_request, max_rounds=1
        )

    assert executed == ["call-1"]
    assert len(continued) == 1
