#!/usr/bin/env python3
"""Validate compliance agent evidence handoffs, never authorize a release."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re

ROLES = (
    "rkn-law",
    "data-mapping",
    "policy-diff",
    "privacy-e2e",
    "incident-drill",
    "compliance-gate",
)
STATUSES = {"pass", "fail", "unknown"}
EVIDENCE_KINDS = {"official-source", "repository", "test-artifact", "runtime-probe"}


def _nonempty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _recent(value: object, now: datetime, max_age: timedelta) -> bool:
    if not isinstance(value, str):
        return False
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return timestamp.utcoffset() == timedelta(0) and timedelta(0) <= now - timestamp <= max_age
    except (ValueError, TypeError, OverflowError):
        return False


def validate_reports(
    reports: list,
    head_sha: str,
    *,
    now: datetime | None = None,
    max_age: timedelta = timedelta(hours=24),
) -> dict:
    """Check completeness/freshness only; evidence content is not authenticated."""
    now = now or datetime.now(timezone.utc)
    errors = []
    seen = set()
    if not isinstance(head_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", head_sha):
        errors.append("expected head must be a full lowercase commit SHA")
    if max_age <= timedelta(0):
        errors.append("maximum evidence age must be positive")
    for index, report in enumerate(reports):
        if not isinstance(report, dict):
            errors.append(f"report {index}: expected an object")
            continue
        role = report.get("role")
        if not isinstance(role, str) or role not in ROLES:
            errors.append(f"report {index}: unknown role")
            continue
        if role in seen:
            errors.append(f"{role}: duplicate report")
        seen.add(role)
        if report.get("head_sha") != head_sha:
            errors.append(f"{role}: reviewed head does not match")
        status = report.get("status")
        if not isinstance(status, str) or status not in STATUSES:
            errors.append(f"{role}: invalid status")
        elif status != "pass":
            errors.append(f"{role}: status is {status}")
        if not _nonempty(report.get("summary")):
            errors.append(f"{role}: missing summary")
        if not _recent(report.get("checked_at"), now, max_age):
            errors.append(f"{role}: missing, stale or invalid UTC check time")
        evidence = report.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            errors.append(f"{role}: missing evidence")
            continue
        for item in evidence:
            if not isinstance(item, dict):
                errors.append(f"{role}: invalid evidence entry")
                continue
            kind = item.get("kind")
            if not isinstance(kind, str) or kind not in EVIDENCE_KINDS:
                errors.append(f"{role}: invalid evidence kind")
            if not _nonempty(item.get("source")):
                errors.append(f"{role}: missing evidence source")
            if not _recent(item.get("checked_at"), now, max_age):
                errors.append(f"{role}: stale or invalid evidence time")
    for role in ROLES:
        if role not in seen:
            errors.append(f"{role}: missing report")
    return {
        "version": 1,
        "head_sha": head_sha,
        "handoff_ready": not errors,
        "release_authorized": False,
        "errors": errors,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--head", required=True, help="Exact reviewed 40-character commit SHA")
    parser.add_argument("reports", nargs="+", type=Path, help="One JSON report per role")
    args = parser.parse_args(argv)
    reports = []
    try:
        for path in args.reports:
            reports.append(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, UnicodeError, ValueError):
        # Avoid echoing report content, paths or potentially sensitive parser errors.
        print(
            json.dumps(
                {
                    "handoff_ready": False,
                    "release_authorized": False,
                    "errors": ["report could not be read as UTF-8 JSON"],
                }
            )
        )
        return 1
    result = validate_reports(reports, args.head)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["handoff_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
