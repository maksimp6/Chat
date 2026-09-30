"""Deterministic reliability SLO evaluator for the Alice Pro agent control plane."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from typing import Any


SLO_TARGET = 0.9999
VALID_STATUSES = {"valid", "invalid", "unknown"}
MATERIAL_MAINTAINER_OUTCOMES = {"merged", "blocked", "deferred", "changes_requested"}
CATASTROPHIC_CODES = {
    "stale_head_merge",
    "behind_master_merge",
    "required_check_bypass",
    "unresolved_review_merge",
    "approval_bypass",
    "cross_scope_leak",
    "secret_exposure",
    "self_authority_expansion",
    "unsafe_production_action",
}


@dataclass(frozen=True)
class ReliabilityFinding:
    severity: str
    code: str
    detail: str
    evidence_id: str | None = None


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def evaluate_snapshot(snapshot: dict[str, Any], *, target: float = SLO_TARGET) -> dict[str, Any]:
    generated_at = _parse_time(snapshot["generated_at"])
    findings: list[ReliabilityFinding] = []
    decisions = list(snapshot.get("decisions") or [])

    valid = 0
    invalid = 0
    unknown = 0
    catastrophic = 0

    for raw in decisions:
        evidence_id = str(raw.get("id") or "").strip() or None
        status = str(raw.get("status") or "unknown").lower()
        if status not in VALID_STATUSES:
            status = "unknown"
            findings.append(
                ReliabilityFinding("high", "invalid_status", "decision status is unsupported", evidence_id)
            )

        provenance = [str(item).strip() for item in raw.get("provenance") or [] if str(item).strip()]
        if status == "valid" and not provenance:
            status = "invalid"
            findings.append(
                ReliabilityFinding(
                    "high",
                    "missing_provenance",
                    "valid control-plane decision has no authoritative provenance",
                    evidence_id,
                )
            )

        violation_code = str(raw.get("violation_code") or "").strip()
        is_catastrophic = bool(raw.get("catastrophic")) or violation_code in CATASTROPHIC_CODES

        if status == "valid":
            valid += 1
        elif status == "invalid":
            invalid += 1
        else:
            unknown += 1

        if is_catastrophic:
            catastrophic += 1
            findings.append(
                ReliabilityFinding(
                    "critical",
                    violation_code or "catastrophic_violation",
                    "catastrophic control-plane violation observed",
                    evidence_id,
                )
            )

    total = valid + invalid + unknown
    reliability = valid / total if total else 0.0

    observer_last_success = snapshot.get("observer_last_success_at")
    if not observer_last_success:
        findings.append(
            ReliabilityFinding("high", "observer_unknown", "Observer freshness cannot be proven")
        )
    else:
        observer_age = generated_at - _parse_time(str(observer_last_success))
        max_age = timedelta(hours=float(snapshot.get("observer_max_age_hours", 2)))
        if observer_age > max_age:
            findings.append(
                ReliabilityFinding(
                    "high",
                    "observer_stale",
                    f"Observer heartbeat is stale by {observer_age}",
                )
            )

    for handoff in snapshot.get("maintainer_handoffs") or []:
        passes = int(handoff.get("observer_passes") or 0)
        outcome = str(handoff.get("outcome") or "").lower()
        if passes >= 2 and outcome not in MATERIAL_MAINTAINER_OUTCOMES:
            findings.append(
                ReliabilityFinding(
                    "high",
                    "maintainer_stall",
                    "maintainer handoff has no material outcome after two Observer passes",
                    str(handoff.get("id") or "") or None,
                )
            )

    strong_retries = int(snapshot.get("strong_retries_without_new_evidence") or 0)
    if strong_retries:
        findings.append(
            ReliabilityFinding(
                "medium",
                "strong_retry_without_evidence",
                f"{strong_retries} strong-model retry/retries had no new evidence",
            )
        )

    duplicate_reads = int(snapshot.get("duplicate_exact_context_reads") or 0)
    if duplicate_reads:
        findings.append(
            ReliabilityFinding(
                "medium",
                "duplicate_exact_context_read",
                f"{duplicate_reads} exact-context read(s) bypassed reusable cache/memory",
            )
        )

    blocking_codes = {
        "invalid_status",
        "missing_provenance",
        "observer_unknown",
        "observer_stale",
        "maintainer_stall",
    }
    blocking = catastrophic > 0 or any(item.code in blocking_codes for item in findings)
    sufficient_evidence = total > 0
    meets_target = sufficient_evidence and reliability >= target
    ready = meets_target and not blocking

    return {
        "ready": ready,
        "target": target,
        "reliability": reliability,
        "decisions_total": total,
        "decisions_valid": valid,
        "decisions_invalid": invalid,
        "decisions_unknown": unknown,
        "catastrophic_violations": catastrophic,
        "error_budget_fraction": max(0.0, 1.0 - target),
        "sufficient_evidence": sufficient_evidence,
        "findings": [asdict(item) for item in findings],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path, help="JSON reliability snapshot")
    parser.add_argument("--pretty", action="store_true")
    parser.add_argument("--target", type=float, default=SLO_TARGET)
    args = parser.parse_args()

    snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
    result = evaluate_snapshot(snapshot, target=args.target)
    print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0 if result["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
