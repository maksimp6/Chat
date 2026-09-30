import json

from agent_office.reliability import evaluate_snapshot
from agent_office.reliability_sources import build_snapshot


def test_observer_snapshot_and_stall_are_real_evidence():
    observer = {
        "generated_at": "2026-09-30T07:17:00+00:00",
        "repo": "maksimp6/Chat",
        "findings": [
            {
                "severity": "medium",
                "kind": "maintainer_stall",
                "number": 548,
                "url": "https://github.com/maksimp6/Chat/pull/548",
            }
        ],
        "threads": [],
    }

    snapshot = build_snapshot(
        observer=observer,
        observer_ref="artifact:agent-observer",
        generated_at="2026-09-30T07:18:00+00:00",
    )

    assert snapshot["observer_last_success_at"] == observer["generated_at"]
    assert snapshot["maintainer_handoffs"] == [
        {"id": "pr:548", "observer_passes": 2, "outcome": "stalled"}
    ]
    result = evaluate_snapshot(snapshot)
    assert result["ready"] is False
    assert "maintainer_stall" in {item["code"] for item in result["findings"]}


def test_missing_observer_metadata_fails_closed():
    snapshot = build_snapshot(
        observer={"findings": [], "threads": []},
        observer_ref="artifact:broken-observer",
        generated_at="2026-09-30T07:18:00+00:00",
    )

    assert snapshot["decisions"][0]["status"] == "unknown"
    assert snapshot["observer_last_success_at"] is None


def test_blocked_readiness_is_a_valid_fail_closed_decision():
    readiness = {
        "ready": False,
        "repo": "maksimp6/Chat",
        "pr_number": 548,
        "head_sha": "abc123",
        "behind_by": 15,
        "blockers": [{"code": "behind_master", "detail": "head is behind"}],
    }

    snapshot = build_snapshot(
        readiness=[(readiness, "artifact:readiness-548")],
        generated_at="2026-09-30T07:18:00+00:00",
    )

    decision = snapshot["decisions"][0]
    assert decision["status"] == "valid"
    assert decision["kind"] == "merge_readiness"


def test_contradictory_ready_with_blockers_is_invalid():
    readiness = {
        "ready": True,
        "repo": "maksimp6/Chat",
        "pr_number": 1,
        "head_sha": "deadbeef",
        "blockers": [{"code": "review_threads"}],
    }

    snapshot = build_snapshot(
        readiness=[(readiness, "artifact:contradictory")],
        generated_at="2026-09-30T07:18:00+00:00",
    )

    assert snapshot["decisions"][0]["status"] == "invalid"


def test_trace_cache_retrieval_and_cost_waste_are_collected():
    trace = {
        "trace_id": "trace-1",
        "context_cache_operations": [
            {
                "status": "hit",
                "repository": "maksimp6/Chat",
                "work_item": "PR#591",
                "head_sha": "head-1",
            }
        ],
        "retrieval_operations": [
            {
                "status": "hit",
                "repository": "maksimp6/Chat",
                "work_item": "PR#591",
                "head_sha": "head-1",
            }
        ],
        "events": [
            {
                "type": "raw_context_read",
                "data": {
                    "repository": "maksimp6/Chat",
                    "work_item": "PR#591",
                    "head_sha": "head-1",
                },
            },
            {
                "type": "raw_context_read",
                "data": {
                    "repository": "maksimp6/Chat",
                    "work_item": "PR#591",
                    "head_sha": "head-1",
                },
            },
            {
                "type": "model_call",
                "data": {"tier": "strong", "evidence_version": "v1"},
            },
            {
                "type": "model_call",
                "data": {"tier": "strong", "evidence_version": "v1"},
            },
        ],
    }

    snapshot = build_snapshot(
        traces=[(trace, "trace-file:trace-1")],
        generated_at="2026-09-30T07:18:00+00:00",
    )

    assert [item["status"] for item in snapshot["decisions"]] == ["valid", "valid"]
    assert snapshot["duplicate_exact_context_reads"] == 1
    assert snapshot["strong_retries_without_new_evidence"] == 1


def test_approval_required_is_not_mistaken_for_approval_success():
    trace = {
        "trace_id": "trace-approval",
        "events": [
            {
                "type": "tool_approval",
                "data": {"phase": "approval_required"},
            }
        ],
    }

    snapshot = build_snapshot(
        traces=[(trace, "trace-file:approval")],
        generated_at="2026-09-30T07:18:00+00:00",
    )

    assert snapshot["decisions"][0]["kind"] == "approval_boundary"
    assert snapshot["decisions"][0]["status"] == "valid"


def test_unknown_approval_phase_does_not_become_success():
    trace = {
        "trace_id": "trace-approval",
        "events": [{"type": "tool_approval", "data": {"phase": "approved"}}],
    }

    snapshot = build_snapshot(
        traces=[(trace, "trace-file:approval")],
        generated_at="2026-09-30T07:18:00+00:00",
    )

    assert snapshot["decisions"][0]["status"] == "unknown"
