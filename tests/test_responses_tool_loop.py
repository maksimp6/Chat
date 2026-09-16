import pytest

from responses_tool_loop import (
    build_continuation_input,
    extract_function_calls,
    make_function_call_output,
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
