"""Deterministic fault-injection matrix for Alice Pro control-plane safeguards."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
import importlib.util
from pathlib import Path
from typing import Any

from agent_office.observer import Event, Thread, detect_findings
from agent_office.reliability import evaluate_snapshot
from trace_security import sanitize_trace_value


ROOT = Path(__file__).resolve().parents[1]
MERGE_READINESS_PATH = ROOT / "scripts" / "merge_readiness.py"


@dataclass(frozen=True)
class FaultResult:
    scenario: str
    detector: str
    detected: bool
    evidence: tuple[str, ...]


def _merge_readiness_module():
    spec = importlib.util.spec_from_file_location("_fault_merge_readiness", MERGE_READINESS_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("merge readiness module cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _merge_snapshot(**overrides: Any) -> dict[str, Any]:
    value = {
        "repo": "maksimp6/Chat",
        "pr_number": 999,
        "base_ref": "master",
        "pr_base_ref": "master",
        "head_sha": "head",
        "base_sha": "base",
        "final_head_sha": "head",
        "final_base_sha": "base",
        "snapshot_changed": False,
        "pr_state": "open",
        "merged": False,
        "draft": False,
        "behind_by": 0,
        "required_checks": ["Application tests"],
        "check_runs": [
            {
                "id": 1,
                "name": "Application tests",
                "status": "completed",
                "conclusion": "success",
            }
        ],
        "review_threads": [{"isResolved": True}],
        "review_threads_truncated": False,
    }
    value.update(overrides)
    return value


def _reliability_snapshot(now: datetime, **overrides: Any) -> dict[str, Any]:
    value = {
        "generated_at": now.isoformat(),
        "observer_last_success_at": (now - timedelta(minutes=15)).isoformat(),
        "decisions": [
            {
                "id": "baseline",
                "status": "valid",
                "provenance": ["fault-matrix:baseline"],
            }
        ],
        "maintainer_handoffs": [],
        "strong_retries_without_new_evidence": 0,
        "duplicate_exact_context_reads": 0,
    }
    value.update(overrides)
    return value


def _reliability_codes(snapshot: dict[str, Any]) -> set[str]:
    result = evaluate_snapshot(snapshot, minimum_decisions=1)
    return {str(item["code"]) for item in result["findings"]}


def run_fault_matrix(*, now: datetime | None = None) -> list[FaultResult]:
    now = now or datetime.now(UTC)
    merge_readiness = _merge_readiness_module()
    results: list[FaultResult] = []

    changed = merge_readiness.evaluate_snapshot(
        _merge_snapshot(final_head_sha="new-head", snapshot_changed=True)
    )
    changed_codes = {item["code"] for item in changed["blockers"]}
    results.append(
        FaultResult(
            "stale_head_snapshot",
            "merge_readiness",
            "snapshot_changed" in changed_codes,
            tuple(sorted(changed_codes)),
        )
    )

    behind = merge_readiness.evaluate_snapshot(_merge_snapshot(behind_by=1))
    behind_codes = {item["code"] for item in behind["blockers"]}
    results.append(
        FaultResult(
            "behind_master",
            "merge_readiness",
            "behind_master" in behind_codes,
            tuple(sorted(behind_codes)),
        )
    )

    missing = merge_readiness.evaluate_snapshot(_merge_snapshot(check_runs=[]))
    missing_codes = {item["code"] for item in missing["blockers"]}
    results.append(
        FaultResult(
            "required_check_missing",
            "merge_readiness",
            "required_check_missing" in missing_codes,
            tuple(sorted(missing_codes)),
        )
    )

    threads = merge_readiness.evaluate_snapshot(
        _merge_snapshot(review_threads=[{"isResolved": False}])
    )
    thread_codes = {item["code"] for item in threads["blockers"]}
    results.append(
        FaultResult(
            "unresolved_review_thread",
            "merge_readiness",
            "review_threads" in thread_codes,
            tuple(sorted(thread_codes)),
        )
    )

    dispatch_at = now - timedelta(hours=3)
    observer_thread = Thread(
        number=999,
        title="maintainer stall",
        url="https://github.com/maksimp6/Chat/pull/999",
        kind="pr",
        state="open",
        owner_agent="human",
        created_at=dispatch_at - timedelta(hours=1),
        events=[
            Event(
                dispatch_at,
                "maksimp6",
                "human",
                "комментарий: @claude maintainer pass",
                kind="commented",
            )
        ],
        checks={"Application tests": "success"},
        dispatches=[("claude", dispatch_at)],
    )
    observer_codes = {finding.kind for finding in detect_findings(observer_thread, now)}
    results.append(
        FaultResult(
            "maintainer_stall",
            "agent_observer",
            "maintainer_stall" in observer_codes,
            tuple(sorted(observer_codes)),
        )
    )

    future_codes = _reliability_codes(
        _reliability_snapshot(
            now,
            observer_last_success_at=(now + timedelta(minutes=1)).isoformat(),
        )
    )
    results.append(
        FaultResult(
            "observer_future_heartbeat",
            "reliability_slo",
            "observer_future" in future_codes,
            tuple(sorted(future_codes)),
        )
    )

    retry_codes = _reliability_codes(
        _reliability_snapshot(now, strong_retries_without_new_evidence=1)
    )
    results.append(
        FaultResult(
            "strong_retry_without_new_evidence",
            "reliability_slo",
            "strong_retry_without_evidence" in retry_codes,
            tuple(sorted(retry_codes)),
        )
    )

    duplicate_codes = _reliability_codes(
        _reliability_snapshot(now, duplicate_exact_context_reads=1)
    )
    results.append(
        FaultResult(
            "duplicate_exact_context_read",
            "reliability_slo",
            "duplicate_exact_context_read" in duplicate_codes,
            tuple(sorted(duplicate_codes)),
        )
    )

    for scenario, code in (
        ("cross_scope_leak", "cross_scope_leak"),
        ("approval_bypass", "approval_bypass"),
        ("self_authority_expansion", "self_authority_expansion"),
        ("unsafe_production_action", "unsafe_production_action"),
    ):
        codes = _reliability_codes(
            _reliability_snapshot(
                now,
                decisions=[
                    {
                        "id": scenario,
                        "status": "valid",
                        "provenance": [f"fault-matrix:{scenario}"],
                        "violation_code": code,
                    }
                ],
            )
        )
        results.append(
            FaultResult(
                scenario,
                "reliability_slo",
                code in codes,
                tuple(sorted(codes)),
            )
        )

    secret = "before -----BEGIN PRIVATE KEY-----\nSECRET\n-----END PRIVATE KEY----- after"
    sanitized = str(sanitize_trace_value(secret))
    results.append(
        FaultResult(
            "private_key_exposure",
            "trace_security",
            "SECRET" not in sanitized and "<redacted-private-key>" in sanitized,
            ("redacted-private-key",) if "SECRET" not in sanitized else ("secret-visible",),
        )
    )

    return results


def report_fault_matrix(*, now: datetime | None = None) -> dict[str, Any]:
    results = run_fault_matrix(now=now)
    return {
        "passed": all(item.detected for item in results),
        "scenario_count": len(results),
        "detected_count": sum(1 for item in results if item.detected),
        "results": [asdict(item) for item in results],
    }
