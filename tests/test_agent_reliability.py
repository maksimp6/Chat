from datetime import UTC, datetime, timedelta

from agent_office.reliability import SLO_TARGET, evaluate_snapshot


NOW = datetime(2026, 9, 30, 7, 0, tzinfo=UTC)


def _snapshot(**overrides):
    values = {
        "generated_at": NOW.isoformat(),
        "observer_last_success_at": (NOW - timedelta(minutes=30)).isoformat(),
        "decisions": [
            {
                "id": "merge:591",
                "kind": "merge_readiness",
                "status": "valid",
                "provenance": ["github:pr:591", "check:merge-readiness"],
            }
        ],
        "maintainer_handoffs": [],
        "strong_retries_without_new_evidence": 0,
        "duplicate_exact_context_reads": 0,
    }
    values.update(overrides)
    return values


def test_single_proven_valid_decision_meets_target():
    result = evaluate_snapshot(_snapshot())
    assert result["ready"] is True
    assert result["reliability"] == 1.0
    assert result["target"] == SLO_TARGET


def test_unknown_evidence_does_not_count_as_success():
    result = evaluate_snapshot(
        _snapshot(decisions=[{"id": "x", "status": "unknown", "provenance": []}])
    )
    assert result["ready"] is False
    assert result["reliability"] == 0.0
    assert result["decisions_unknown"] == 1


def test_valid_decision_without_provenance_fails_closed():
    result = evaluate_snapshot(
        _snapshot(decisions=[{"id": "x", "status": "valid", "provenance": []}])
    )
    assert result["ready"] is False
    assert result["decisions_invalid"] == 1
    assert {item["code"] for item in result["findings"]} == {"missing_provenance"}


def test_catastrophic_violation_has_zero_error_budget():
    result = evaluate_snapshot(
        _snapshot(
            decisions=[
                {
                    "id": "merge:bad",
                    "status": "invalid",
                    "provenance": ["github:pr:1"],
                    "violation_code": "stale_head_merge",
                }
            ]
        )
    )
    assert result["ready"] is False
    assert result["catastrophic_violations"] == 1


def test_observer_staleness_blocks_reliability_claim():
    result = evaluate_snapshot(
        _snapshot(observer_last_success_at=(NOW - timedelta(hours=3)).isoformat())
    )
    assert result["ready"] is False
    assert "observer_stale" in {item["code"] for item in result["findings"]}


def test_maintainer_stall_after_two_passes_blocks():
    result = evaluate_snapshot(
        _snapshot(
            maintainer_handoffs=[
                {"id": "pr:548", "observer_passes": 2, "outcome": "acknowledged"}
            ]
        )
    )
    assert result["ready"] is False
    assert "maintainer_stall" in {item["code"] for item in result["findings"]}


def test_material_maintainer_outcome_is_accepted():
    result = evaluate_snapshot(
        _snapshot(
            maintainer_handoffs=[{"id": "pr:591", "observer_passes": 2, "outcome": "blocked"}]
        )
    )
    assert result["ready"] is True


def test_cost_waste_signals_are_visible_but_not_false_catastrophes():
    result = evaluate_snapshot(
        _snapshot(strong_retries_without_new_evidence=2, duplicate_exact_context_reads=3)
    )
    codes = {item["code"] for item in result["findings"]}
    assert result["ready"] is True
    assert result["catastrophic_violations"] == 0
    assert "strong_retry_without_evidence" in codes
    assert "duplicate_exact_context_read" in codes


def test_no_decisions_means_insufficient_evidence():
    result = evaluate_snapshot(_snapshot(decisions=[]))
    assert result["ready"] is False
    assert result["sufficient_evidence"] is False
