from pathlib import Path

import pytest

from agent_rules.controller import GuardedTask, TaskScope, load_registry
from tool_providers.universal_tool_platform import UniversalToolCall, UniversalToolExecutor


class Trace:
    def __init__(self):
        self.events = []

    def add_event(self, kind, payload):
        self.events.append((kind, payload))


class Registry:
    def __init__(self):
        self.calls = 0
        self.result = {"success": True, "data": {"receipt": "observed"}}

    def get_universal_definition(self, name):
        return {"requires_approval": False, "inputSchema": {"type": "object"}}

    def execute(self, *args, **kwargs):
        self.calls += 1
        return self.result


@pytest.fixture
def task():
    registry = Registry()
    trace = Trace()
    scope = TaskScope("task", "chatgpt", "redmi9", "discussions")
    controller = GuardedTask(
        scope,
        UniversalToolExecutor(registry),
        trace,
        observe_device=lambda: "redmi9",
        observe_destination=lambda call: "discussions",
        verify_result=lambda result: result.get("data") == {"receipt": "observed"},
    )
    return controller, registry, trace


def test_registry_proposed_and_complete():
    rules = load_registry(Path("agent_rules/registry.json"))
    assert len(rules) == 6
    assert all(rule["state"] == "proposed" for rule in rules)


def test_device_mismatch_blocks_before_dispatch(task):
    guard, registry, trace = task
    guard.observe_device = lambda: "xiaomi16"
    assert guard.execute(UniversalToolCall("open", {}))["status"] == "BLOCKED"
    assert registry.calls == 0
    assert trace.events[-1][1]["rule_id"] == "AR-001"


def test_issue_substitution_fails_before_write(task):
    guard, registry, _ = task
    guard.observe_destination = lambda call: "issues"
    assert guard.execute(UniversalToolCall("write", {}))["status"] == "FAILED"
    assert registry.calls == 0


def test_text_is_not_action(task):
    guard, _, trace = task
    assert guard.finish(claim_success=False)["status"] == "FAILED"
    assert trace.events[-1][1]["rule_id"] == "AR-002"


def test_success_needs_verified_receipt(task):
    guard, registry, trace = task
    registry.result = {"success": True, "data": "done"}
    assert guard.execute(UniversalToolCall("open", {}))["status"] == "FAILED"
    assert guard.finish(claim_success=True)["status"] == "FAILED"
    assert trace.events[-1][1]["rule_id"] == "AR-003"


def test_failed_method_blocked_even_new_call_id(task):
    guard, registry, trace = task
    registry.result = {"success": False, "error": "secret error"}
    call = UniversalToolCall("open", {"token": "secret"}, call_id="a")
    guard.execute(call)
    assert (
        guard.execute(UniversalToolCall("open", call.arguments, call_id="b"))["status"] == "BLOCKED"
    )
    assert registry.calls == 1
    assert "secret" not in str(trace.events)
    assert any(event[1]["rule_id"] == "AR-005" for event in trace.events)
    proposal = guard.proposals[0]
    assert proposal["state"] == "proposed"
    assert proposal["regression"]["expected"] == "BLOCKED"


def test_verified_completion_and_closed_guard(task):
    guard, registry, _ = task
    assert guard.execute(UniversalToolCall("open", {}))["status"] == "VERIFIED"
    assert guard.finish(claim_success=True)["status"] == "VERIFIED"
    with pytest.raises(RuntimeError):
        guard.execute(UniversalToolCall("open", {}))
    assert registry.calls == 1


def test_unknown_identity_and_target_rejected():
    with pytest.raises(ValueError):
        TaskScope("task", "", "", "discussions")


def test_registry_cannot_weaken(tmp_path):
    path = tmp_path / "rules.json"
    path.write_text('{"rules": []}')
    with pytest.raises(ValueError):
        load_registry(path)


def test_observer_failure_is_closed(task):
    guard, registry, _ = task

    def fail():
        raise RuntimeError("secret")

    guard.observe_device = fail
    assert guard.execute(UniversalToolCall("open", {}))["status"] == "BLOCKED"
    assert registry.calls == 0


def test_destination_observer_raises(task):
    guard, registry, _ = task

    def fail(call):
        raise RuntimeError("secret")

    guard.observe_destination = fail
    assert guard.execute(UniversalToolCall("open", {}))["rule_id"] == "AR-004"
    assert registry.calls == 0


def test_verifier_raises(task):
    guard, registry, _ = task

    def fail(result):
        raise RuntimeError("secret")

    guard.verify_result = fail
    assert guard.execute(UniversalToolCall("open", {}))["rule_id"] == "AR-003"
    assert registry.calls == 1


def test_finish_twice_rejected(task):
    guard, _, _ = task
    guard.finish(claim_success=False)
    with pytest.raises(RuntimeError):
        guard.finish(claim_success=True)


def test_no_success_claim_reports_failed(task):
    guard, _, _ = task
    guard.execute(UniversalToolCall("open", {}))
    assert guard.finish(claim_success=False) == {"success": False, "status": "FAILED"}


@pytest.mark.parametrize(
    "change", ["severity", "activation", "duplicate", "unknown", "description"]
)
def test_registry_rejects_policy_edits(tmp_path, change):
    import json

    document = json.loads(Path("agent_rules/registry.json").read_text())
    rule = document["rules"][0]
    if change == "severity":
        rule["violation"] = "REVIEW"
    elif change == "activation":
        rule["state"] = "active"
    elif change == "duplicate":
        document["rules"][1] = rule
    elif change == "unknown":
        rule["id"] = "AR-007"
    else:
        rule["description"] = ""
    path = tmp_path / "rules.json"
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError):
        load_registry(path)


def test_invalid_arguments_do_not_dispatch(task):
    guard, registry, _ = task
    assert guard.execute(UniversalToolCall("open", {"value": float("nan")}))["status"] == "BLOCKED"
    assert registry.calls == 0


def test_executor_lookup_crash_is_closed(task):
    guard, registry, _ = task

    def fail(name):
        raise RuntimeError("secret")

    registry.get_universal_definition = fail
    assert guard.execute(UniversalToolCall("open", {}))["status"] == "FAILED"
    assert registry.calls == 0


def test_proposals_are_copy_safe(task):
    guard, registry, _ = task
    registry.result = {"success": False}
    guard.execute(UniversalToolCall("open", {}))
    guard.execute(UniversalToolCall("open", {}))
    guard.proposals[0]["state"] = "active"
    assert guard.proposals[0]["state"] == "proposed"


def test_existing_approval_gate_not_bypassed(task):
    guard, registry, _ = task
    registry.get_universal_definition = lambda name: {"requires_approval": True}
    assert guard.execute(UniversalToolCall("open", {}))["success"] is False
    assert registry.calls == 0


def test_real_execution_trace_correlation(task):
    from observability.trace_manager import ExecutionTrace

    guard, registry, _ = task
    trace = ExecutionTrace(trace_id="trace-ar")
    trace.set_context(invocation_id="invocation-ar", agent_id="chatgpt")
    guard.trace = trace
    guard.observe_device = lambda: "wrong"
    guard.execute(UniversalToolCall("open", {}))
    event = trace.trace["events"][-1]
    assert event["type"] == "agent_rule_violation"
    assert event["payload"]["rule_id"] == "AR-001"
    assert trace.trace["context"]["invocation_id"] == "invocation-ar"
    assert registry.calls == 0


def test_repeated_errors_create_proposal_even_changed_method(task):
    guard, registry, trace = task
    registry.result = {"success": False}
    guard.execute(UniversalToolCall("first", {}))
    guard.execute(UniversalToolCall("second", {}))
    assert registry.calls == 2
    assert trace.events[-1][1]["rule_id"] == "AR-005"
    assert len(guard.proposals) == 1
    guard.execute(UniversalToolCall("second", {}))
    guard.execute(UniversalToolCall("second", {}))
    assert len(guard.proposals) == 1
    assert registry.calls == 2


def test_reordered_arguments_cannot_bypass(task):
    guard, registry, _ = task
    registry.result = {"success": False}
    guard.execute(UniversalToolCall("open", {"a": 1, "b": 2}))
    assert guard.execute(UniversalToolCall("open", {"b": 2, "a": 1}))["status"] == "BLOCKED"
    assert registry.calls == 1
