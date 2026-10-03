from datetime import UTC, datetime

from agent_office.fault_matrix import report_fault_matrix, run_fault_matrix


NOW = datetime(2026, 9, 30, 7, 0, tzinfo=UTC)


EXPECTED_SCENARIOS = {
    "stale_head_snapshot",
    "behind_master",
    "required_check_missing",
    "unresolved_review_thread",
    "maintainer_stall",
    "observer_future_heartbeat",
    "strong_retry_without_new_evidence",
    "duplicate_exact_context_read",
    "cross_scope_leak",
    "approval_bypass",
    "self_authority_expansion",
    "unsafe_production_action",
    "private_key_exposure",
}


def test_fault_matrix_detects_every_supported_fault():
    results = run_fault_matrix(now=NOW)
    assert {item.scenario for item in results} == EXPECTED_SCENARIOS
    assert all(item.detected for item in results)


def test_fault_matrix_report_is_machine_readable_and_complete():
    report = report_fault_matrix(now=NOW)
    assert report["passed"] is True
    assert report["scenario_count"] == len(EXPECTED_SCENARIOS)
    assert report["detected_count"] == len(EXPECTED_SCENARIOS)
    assert all(item["evidence"] for item in report["results"])


def test_fault_matrix_uses_multiple_independent_detectors():
    report = report_fault_matrix(now=NOW)
    detectors = {item["detector"] for item in report["results"]}
    assert detectors == {
        "merge_readiness",
        "agent_observer",
        "reliability_slo",
        "trace_security",
    }
