"""Restored private state must preserve authority, input and delivery cursors.

These fixtures are exported through the real public runner API. Authenticating
a transport does not make malformed plaintext safe to restore: no rejected
record may create live state, native history or a provider invocation.
"""

from __future__ import annotations

import copy

import pytest

from tests.test_development_session_contract import harness


@pytest.fixture
def checkpoint_harness(harness):
    from agent_office.claude_runner import PersistentClaudeRunner

    source, _, spy, workspace, _, state_dir = harness
    session = source.open_session(
        "Preserve this goal and pending instructions", work_items=[{"type": "issue", "id": "703"}]
    )
    source.enqueue(session, "completed-comment", "Remember the approved goal")
    source.run_pending(session)
    source.enqueue(session, "pending-comment-1", "Preserve the first undelivered instruction")
    source.enqueue(session, "pending-comment-2", "Preserve the second undelivered instruction")
    record = source.export_checkpoint(session)
    destination_state = state_dir.parent / "restored-state"
    destination_config = state_dir.parent / "restored-claude"
    destination = PersistentClaudeRunner(
        state_dir=destination_state, workspace=workspace, claude_config_dir=destination_config
    )
    return source, session, spy, record, destination, destination_state, destination_config


def _assert_restore_rejected(checkpoint_harness, record):
    _, _, spy, _, destination, destination_state, destination_config = checkpoint_harness
    before_calls = len(spy.calls)
    with pytest.raises(ValueError):
        destination.restore_checkpoint(**record)
    assert len(spy.calls) == before_calls
    assert list((destination_state / "sessions").glob("*.json")) == []
    assert list((destination_state / "snapshots").rglob("*.jsonl")) == []
    assert not destination_config.exists()


def _set_field(record, path, replacement):
    target = record
    for component in path[:-1]:
        target = target[component]
    target[path[-1]] = replacement


@pytest.mark.parametrize(
    "path,replacement",
    [
        pytest.param(("state",), [], id="nonmapping-state"),
        pytest.param(("state", "schema"), True, id="boolean-schema"),
        pytest.param(("state", "schema"), 2, id="unsupported-schema"),
        pytest.param(("state", "goal"), None, id="nontext-goal"),
        pytest.param(("state", "goal"), " \n", id="empty-goal"),
        pytest.param(("state", "goal"), "g" * (128 * 1024 + 1), id="oversize-goal"),
        pytest.param(
            ("state", "goal"),
            "A different mission must have a different identity",
            id="different-goal-same-session",
        ),
        pytest.param(("native_session_id",), [], id="nontext-native-identity"),
        pytest.param(("native_session_id",), "not-a-uuid", id="invalid-native-identity"),
        pytest.param(("state", "role"), {"name": "dialogue"}, id="nontext-role"),
        pytest.param(("state", "role_epoch"), True, id="boolean-role-epoch"),
        pytest.param(("state", "role_epoch"), -1, id="negative-role-epoch"),
        pytest.param(("state", "role_epoch"), "0", id="noninteger-role-epoch"),
        pytest.param(("state", "turn_count"), True, id="boolean-turn-count"),
        pytest.param(("state", "turn_count"), 0, id="turn-count-loses-completed-work"),
        pytest.param(("state", "turn_count"), 2, id="turn-count-invents-completed-work"),
        pytest.param(("state", "status"), ["sleeping"], id="nontext-status"),
        pytest.param(("state", "status"), "deployed", id="unsupported-status"),
        pytest.param(("state", "reason"), "session_closed", id="sleeping-with-closed-reason"),
        pytest.param(("state", "reason"), ["awaiting_comment"], id="nontext-reason"),
        pytest.param(("state", "work_items"), {}, id="nonlist-work-items"),
        pytest.param(("state", "work_items", 0, "type"), "deployment", id="unsupported-work-item"),
        pytest.param(("state", "work_items", 0, "id"), "../703", id="invalid-work-item-identity"),
        pytest.param(("state", "queue"), {}, id="nonlist-queue"),
        pytest.param(("state", "queue", 0, "message"), None, id="nontext-pending-instruction"),
        pytest.param(("state", "queue", 0, "message"), " \n", id="empty-pending-instruction"),
        pytest.param(
            ("state", "queue", 0, "message"),
            "m" * (128 * 1024 + 1),
            id="oversize-pending-instruction",
        ),
        pytest.param(("state", "queue", 0, "event_id"), " \n", id="empty-queued-event"),
        pytest.param(("state", "queue", 0, "event_id"), "e" * 257, id="oversize-queued-event"),
        pytest.param(("state", "received_event_ids"), {}, id="nonlist-received-cursor"),
        pytest.param(("state", "completed_event_ids"), {}, id="nonlist-completed-cursor"),
        pytest.param(("state", "received_event_ids", 1), None, id="nontext-received-event"),
        pytest.param(("state", "results"), {}, id="nonlist-results"),
        pytest.param(
            ("state", "results", 0, "event_id"),
            "pending-comment-1",
            id="result-for-undelivered-event",
        ),
        pytest.param(
            ("state", "results", 0, "provider_session_id"),
            "dc462c2c-bf29-4cce-8b18-6ba331061f09",
            id="foreign-provider-result",
        ),
        pytest.param(("state", "results", 0, "answer"), {"command": "deploy"}, id="nontext-answer"),
        pytest.param(
            ("state", "results", 0, "answer"), "a" * (128 * 1024 + 1), id="oversize-answer"
        ),
        pytest.param(("state", "results", 0, "usage"), [], id="nonmapping-usage"),
        pytest.param(("state", "results", 0, "usage"), {"tokens": True}, id="boolean-token-count"),
        pytest.param(("state", "results", 0, "usage"), {"tokens": -1}, id="negative-token-count"),
        pytest.param(
            ("state", "results", 0, "usage"), {"tokens": float("inf")}, id="infinite-token-count"
        ),
        pytest.param(
            ("state", "results", 0, "usage"), {"tokens": 10**400}, id="unrepresentable-token-count"
        ),
        pytest.param(
            ("state", "results", 0, "usage"), {"tokens": "100"}, id="nonnumber-token-count"
        ),
        pytest.param(
            ("state", "results", 0, "usage"),
            {"private": {"command": "deploy"}},
            id="nested-usage-data",
        ),
        pytest.param(("state", "results", 0, "usage"), {" ": 1}, id="empty-usage-name"),
        pytest.param(("state", "results", 0, "total_cost_usd"), True, id="boolean-cost"),
        pytest.param(("state", "results", 0, "total_cost_usd"), -0.25, id="negative-cost"),
        pytest.param(("state", "results", 0, "total_cost_usd"), float("nan"), id="nan-cost"),
        pytest.param(("state", "results", 0, "total_cost_usd"), 10**400, id="unrepresentable-cost"),
        pytest.param(("state", "results", 0, "total_cost_usd"), "0.25", id="nonnumber-cost"),
        pytest.param(("transcript",), "must remain opaque bytes", id="nonbyte-transcript"),
    ],
)
def test_authenticated_but_malformed_checkpoint_has_no_live_restore(
    checkpoint_harness, path, replacement
):
    record = copy.deepcopy(checkpoint_harness[3])
    _set_field(record, path, replacement)
    _assert_restore_rejected(checkpoint_harness, record)


@pytest.mark.parametrize(
    "mutation",
    [
        "lost-pending-event",
        "completed-not-received",
        "duplicate-received-event",
        "duplicate-completed-event",
        "queued-already-completed",
        "duplicate-queued-event",
        "reordered-pending-commands",
        "lost-completed-result",
        "extra-pending-result",
    ],
)
def test_checkpoint_cursor_cannot_lose_reorder_or_replay_a_command(checkpoint_harness, mutation):
    record = copy.deepcopy(checkpoint_harness[3])
    state = record["state"]
    if mutation == "lost-pending-event":
        state["received_event_ids"].remove("pending-comment-1")
    elif mutation == "completed-not-received":
        state["received_event_ids"].remove("completed-comment")
    elif mutation == "duplicate-received-event":
        state["received_event_ids"].append("pending-comment-2")
    elif mutation == "duplicate-completed-event":
        state["completed_event_ids"].append("completed-comment")
    elif mutation == "queued-already-completed":
        state["queue"][0]["event_id"] = "completed-comment"
    elif mutation == "duplicate-queued-event":
        state["queue"].append(copy.deepcopy(state["queue"][0]))
    elif mutation == "reordered-pending-commands":
        state["queue"].reverse()
    elif mutation == "lost-completed-result":
        state["results"].clear()
    else:
        state["results"].append(copy.deepcopy(state["results"][0]))
    _assert_restore_rejected(checkpoint_harness, record)


@pytest.mark.parametrize(
    "mutation",
    [
        "sleeping-inflight",
        "running-no-inflight",
        "pre-spawn-with-inflight",
        "foreign-event",
        "invalid-operation",
        "extra-operation-authority",
    ],
)
def test_checkpoint_cannot_invent_or_hide_an_unresolved_invocation(checkpoint_harness, mutation):
    record = copy.deepcopy(checkpoint_harness[3])
    state = record["state"]
    state["status"] = "blocked"
    state["reason"] = "provider_outcome_unknown"
    state["in_flight"] = {
        "event_id": "pending-comment-1",
        "operation_id": "dc462c2c-bf29-4cce-8b18-6ba331061f09",
    }
    if mutation == "sleeping-inflight":
        state.update(status="sleeping", reason="awaiting_comment")
    elif mutation == "running-no-inflight":
        state.update(status="running", reason="running_command", in_flight=None)
    elif mutation == "pre-spawn-with-inflight":
        state["reason"] = "pre_spawn_failed"
    elif mutation == "foreign-event":
        state["in_flight"]["event_id"] = "not-the-pending-command"
    elif mutation == "invalid-operation":
        state["in_flight"]["operation_id"] = "unknown-operation"
    else:
        state["in_flight"]["capabilities"] = ["Bash", "deploy"]
    _assert_restore_rejected(checkpoint_harness, record)


@pytest.mark.parametrize("target", ["instruction", "result"])
def test_checkpoint_does_not_import_unrecognized_authority_fields(checkpoint_harness, target):
    record = copy.deepcopy(checkpoint_harness[3])
    selected = record["state"]
    if target == "instruction":
        selected = selected["queue"][0]
    elif target == "result":
        selected = selected["results"][0]
    selected["capabilities"] = ["Bash", "Write", "deploy"]
    _assert_restore_rejected(checkpoint_harness, record)


def test_never_started_session_roundtrip_preserves_pending_input_without_fake_history(harness):
    from agent_office.claude_runner import PersistentClaudeRunner

    source, _, spy, workspace, _, state_dir = harness
    session = source.open_session("A queued mission can survive before its first turn")
    source.enqueue(session, "initial-comment", "Start only when the worker wakes")
    record = source.export_checkpoint(session)
    assert record["transcript"] == b""
    destination = PersistentClaudeRunner(
        state_dir=state_dir.parent / "initial-restored",
        workspace=workspace,
        claude_config_dir=state_dir.parent / "initial-config",
    )
    assert destination.restore_checkpoint(**record) == session
    assert spy.calls == []
    assert destination.get_state(session)["queue"] == [
        {"event_id": "initial-comment", "message": "Start only when the worker wakes"}
    ]
    destination.run_pending(session)
    assert len(spy.calls) == 1
    assert "--session-id" in spy.calls[0]["argv"]
    assert destination.get_state(session)["completed_event_ids"] == ["initial-comment"]


def test_ambiguous_first_spawn_without_transcript_stays_blocked_after_restore(harness):
    from agent_office.claude_runner import PersistentClaudeRunner

    source, _, spy, workspace, _, state_dir = harness
    session = source.open_session("An unknown first native outcome cannot be retried")
    source.enqueue(session, "initial-comment", "This command may already have been paid for")
    spy.fail_once = True
    assert source.run_pending(session)["reason"] == "provider_outcome_unknown"
    record = source.export_checkpoint(session)
    assert record["transcript"] == b""
    destination = PersistentClaudeRunner(
        state_dir=state_dir.parent / "unknown-restored",
        workspace=workspace,
        claude_config_dir=state_dir.parent / "unknown-config",
    )
    assert destination.restore_checkpoint(**record) == session
    state = destination.run_pending(session)
    assert state["status"] == "blocked"
    assert state["reason"] == "provider_outcome_unknown"
    assert state["completed_event_ids"] == []
    assert state["queue"][0]["event_id"] == "initial-comment"
    assert len(spy.calls) == 1
