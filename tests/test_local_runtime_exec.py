import subprocess
from unittest.mock import patch

from tool_registry import ToolRegistry
from trace_manager import ExecutionTrace
from universal_tool_platform import UniversalToolCall, UniversalToolExecutor


def test_local_runtime_exec_runs_on_backend_host(tmp_path):
    import runtime_tools

    completed = subprocess.CompletedProcess(
        args=["/bin/bash", "-lc", "pwd"],
        returncode=0,
        stdout=f"{tmp_path}\n",
        stderr="",
    )
    with patch("runtime_tools.subprocess.run", return_value=completed) as run:
        result = runtime_tools.local_runtime_exec(
            {
                "command": "pwd",
                "cwd": str(tmp_path),
                "timeout_seconds": 5,
            }
        )

    assert result["success"] is True
    assert result["exit_code"] == 0
    assert result["cwd"] == str(tmp_path)
    assert result["timeout_seconds"] == 5
    assert result["stdout"] == f"{tmp_path}\n"

    argv = run.call_args.args[0]
    assert argv == ["/bin/bash", "-lc", "pwd"]
    assert run.call_args.kwargs["shell"] is False
    assert run.call_args.kwargs["stdin"] is subprocess.DEVNULL
    assert run.call_args.kwargs["cwd"] == str(tmp_path)
    assert run.call_args.kwargs["timeout"] == 5


def test_local_runtime_exec_reports_timeout(tmp_path):
    import runtime_tools

    timeout = subprocess.TimeoutExpired(
        ["/bin/bash", "-lc", "sleep 10"],
        2,
        output="partial stdout",
        stderr="partial stderr",
    )
    with patch("runtime_tools.subprocess.run", side_effect=timeout):
        result = runtime_tools.local_runtime_exec(
            {
                "command": "sleep 10",
                "cwd": str(tmp_path),
                "timeout_seconds": 2,
            }
        )

    assert result["success"] is False
    assert result["exit_code"] is None
    assert result["stdout"] == "partial stdout"
    assert result["stderr"] == "partial stderr"
    assert result["error"] == "Local command timed out after 2s"


def test_local_runtime_exec_is_registered_as_approval_required():
    registry = ToolRegistry()
    meta = registry.get_tool_meta("local_runtime_exec")

    assert meta is not None
    assert meta["risk_level"] == "high"
    assert meta["read_only"] is False
    assert meta["requires_approval"] is True
    assert "local_runtime_exec" in registry.get_available_categories()["runtime"]


def test_local_runtime_exec_uses_existing_approval_and_trace_pipeline(tmp_path):
    registry = ToolRegistry()
    executor = UniversalToolExecutor(registry)
    trace = ExecutionTrace()
    call = UniversalToolCall(
        tool_name="local_runtime_exec",
        arguments={
            "command": "printf secret",
            "cwd": str(tmp_path),
            "timeout_seconds": 5,
        },
        transport="responses_api",
        call_id="local-call-1",
        approved=False,
    )

    approval_result = executor.execute(call)
    assert approval_result["success"] is False
    assert approval_result["metadata"]["phase"] == "approval_required"

    completed = subprocess.CompletedProcess(
        args=["/bin/bash", "-lc", "printf secret"],
        returncode=0,
        stdout="secret",
        stderr="",
    )
    approved_call = UniversalToolCall(
        tool_name="local_runtime_exec",
        arguments=dict(call.arguments),
        transport="responses_api",
        call_id="local-call-1",
        approved=True,
    )

    with patch("runtime_tools.subprocess.run", return_value=completed):
        result = executor.execute_with_trace(approved_call, trace)

    assert result["success"] is True
    tool_call = trace.trace["tool_calls"][0]
    assert tool_call["arguments"]["command"] == "<redacted>"
    assert tool_call["result"]["data"]["stdout"] == "<redacted>"

    runtime_events = [
        event for event in trace.trace["events"] if event["type"].startswith("runtime_")
    ]
    assert [event["type"] for event in runtime_events] == [
        "runtime_started",
        "runtime_finished",
    ]
    assert runtime_events[0]["payload"]["runtime"] == "local"
    assert runtime_events[1]["payload"]["exit_code"] == 0
