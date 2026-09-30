"""Build reliability snapshots from real deterministic Alice Pro evidence."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any, Iterable


_CACHE_STATUSES = {"hit", "partial", "miss"}
_RETRIEVAL_STATUSES = {"hit", "miss"}
_BLOCKING_APPROVAL_PHASES = {"approval_required", "authorization_denied", "approval_denied"}


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return value


def _provenance(*values: str | None) -> list[str]:
    return [str(value).strip() for value in values if str(value or "").strip()]


def _decision(
    *,
    decision_id: str,
    kind: str,
    status: str,
    provenance: Iterable[str],
) -> dict[str, Any]:
    return {
        "id": decision_id,
        "kind": kind,
        "status": status,
        "provenance": list(provenance),
    }


def _observer_evidence(
    observer: dict[str, Any],
    *,
    source_ref: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str | None]:
    decisions: list[dict[str, Any]] = []
    handoffs: list[dict[str, Any]] = []

    generated_at = str(observer.get("generated_at") or "").strip() or None
    repo = str(observer.get("repo") or "").strip()
    if generated_at and repo:
        decisions.append(
            _decision(
                decision_id=f"observer:{generated_at}",
                kind="observer_snapshot",
                status="valid",
                provenance=_provenance(source_ref, f"github:repo:{repo}"),
            )
        )
    else:
        decisions.append(
            _decision(
                decision_id="observer:unknown",
                kind="observer_snapshot",
                status="unknown",
                provenance=_provenance(source_ref),
            )
        )

    for index, finding in enumerate(observer.get("findings") or []):
        if not isinstance(finding, dict):
            continue
        kind = str(finding.get("kind") or "unknown")
        number = str(finding.get("number") or "")
        evidence_id = f"observer-finding:{kind}:{number or index}"
        decisions.append(
            _decision(
                decision_id=evidence_id,
                kind="observer_finding",
                status="valid",
                provenance=_provenance(
                    source_ref,
                    str(finding.get("url") or ""),
                    f"github:{repo}#{number}" if repo and number else None,
                ),
            )
        )
        if kind == "maintainer_stall":
            handoffs.append(
                {
                    "id": f"pr:{number}" if number else evidence_id,
                    "observer_passes": 2,
                    "outcome": "stalled",
                }
            )

    return decisions, handoffs, generated_at


def _readiness_evidence(
    readiness: dict[str, Any],
    *,
    source_ref: str,
) -> dict[str, Any]:
    repo = str(readiness.get("repo") or "").strip()
    pr_number = readiness.get("pr_number")
    head_sha = str(readiness.get("head_sha") or "").strip()
    ready = readiness.get("ready")
    blockers = readiness.get("blockers")

    structurally_valid = (
        bool(repo)
        and pr_number is not None
        and bool(head_sha)
        and isinstance(ready, bool)
        and isinstance(blockers, list)
    )
    consistent = structurally_valid and not (ready and bool(blockers))
    status = "valid" if consistent else "invalid"

    return _decision(
        decision_id=f"merge-readiness:{repo}#{pr_number}@{head_sha or 'unknown'}",
        kind="merge_readiness",
        status=status,
        provenance=_provenance(
            source_ref,
            f"github:pr:{repo}#{pr_number}@{head_sha}"
            if repo and pr_number is not None and head_sha
            else None,
        ),
    )


def _trace_evidence(
    trace: dict[str, Any],
    *,
    source_ref: str,
) -> tuple[list[dict[str, Any]], int, int]:
    decisions: list[dict[str, Any]] = []
    duplicate_exact_context_reads = 0
    strong_retries_without_new_evidence = 0

    trace_id = str(trace.get("trace_id") or trace.get("id") or "").strip()
    trace_ref = f"trace:{trace_id}" if trace_id else source_ref

    cache_ops = trace.get("context_cache_operations") or []
    for index, operation in enumerate(cache_ops):
        if not isinstance(operation, dict):
            continue
        status = str(operation.get("status") or "").lower()
        head_sha = str(operation.get("head_sha") or "").strip()
        repository = str(operation.get("repository") or "").strip()
        work_item = str(operation.get("work_item") or "").strip()
        valid = status in _CACHE_STATUSES and bool(head_sha and repository and work_item)
        decisions.append(
            _decision(
                decision_id=f"cache:{trace_id or 'trace'}:{index}",
                kind="context_cache",
                status="valid" if valid else "invalid",
                provenance=_provenance(
                    source_ref,
                    trace_ref,
                    f"github:{repository}:{work_item}@{head_sha}" if valid else None,
                ),
            )
        )

    retrieval_ops = trace.get("retrieval_operations") or []
    for index, operation in enumerate(retrieval_ops):
        if not isinstance(operation, dict):
            continue
        status = str(operation.get("status") or "").lower()
        head_sha = str(operation.get("head_sha") or "").strip()
        repository = str(operation.get("repository") or "").strip()
        work_item = str(operation.get("work_item") or "").strip()
        valid = status in _RETRIEVAL_STATUSES and bool(head_sha and repository and work_item)
        decisions.append(
            _decision(
                decision_id=f"retrieval:{trace_id or 'trace'}:{index}",
                kind="hybrid_retrieval",
                status="valid" if valid else "invalid",
                provenance=_provenance(
                    source_ref,
                    trace_ref,
                    f"github:{repository}:{work_item}@{head_sha}" if valid else None,
                ),
            )
        )

    events = trace.get("events") or []
    exact_context_seen: set[tuple[str, str, str]] = set()
    last_strong_evidence: str | None = None
    for event in events:
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("type") or "")
        payload = event.get("data") or event.get("payload") or {}
        if not isinstance(payload, dict):
            payload = {}

        if event_type in {"context_source_read", "raw_context_read"}:
            key = (
                str(payload.get("repository") or ""),
                str(payload.get("work_item") or ""),
                str(payload.get("head_sha") or ""),
            )
            if all(key) and key in exact_context_seen:
                duplicate_exact_context_reads += 1
            elif all(key):
                exact_context_seen.add(key)

        if (
            event_type in {"strong_model_call", "model_call"}
            and str(payload.get("tier") or "").lower() == "strong"
        ):
            evidence_version = str(payload.get("evidence_version") or "").strip()
            if evidence_version and evidence_version == last_strong_evidence:
                strong_retries_without_new_evidence += 1
            if evidence_version:
                last_strong_evidence = evidence_version

        if event_type in {"tool_authorization", "tool_approval"}:
            phase = str(payload.get("phase") or "").lower()
            decision_status = "valid" if phase in _BLOCKING_APPROVAL_PHASES else "unknown"
            decisions.append(
                _decision(
                    decision_id=f"approval:{trace_id or 'trace'}:{len(decisions)}",
                    kind="approval_boundary",
                    status=decision_status,
                    provenance=_provenance(source_ref, trace_ref),
                )
            )

    return decisions, duplicate_exact_context_reads, strong_retries_without_new_evidence


def build_snapshot(
    *,
    observer: dict[str, Any] | None = None,
    observer_ref: str | None = None,
    readiness: list[tuple[dict[str, Any], str]] | None = None,
    traces: list[tuple[dict[str, Any], str]] | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    decisions: list[dict[str, Any]] = []
    maintainer_handoffs: list[dict[str, Any]] = []
    observer_last_success_at: str | None = None
    duplicate_reads = 0
    strong_retries = 0

    if observer is not None:
        observer_decisions, handoffs, observer_generated_at = _observer_evidence(
            observer,
            source_ref=observer_ref or "observer:inline",
        )
        decisions.extend(observer_decisions)
        maintainer_handoffs.extend(handoffs)
        observer_last_success_at = observer_generated_at

    for payload, source_ref in readiness or []:
        decisions.append(_readiness_evidence(payload, source_ref=source_ref))

    for payload, source_ref in traces or []:
        trace_decisions, trace_duplicates, trace_retries = _trace_evidence(
            payload,
            source_ref=source_ref,
        )
        decisions.extend(trace_decisions)
        duplicate_reads += trace_duplicates
        strong_retries += trace_retries

    return {
        "generated_at": generated_at or datetime.now(UTC).isoformat(),
        "observer_last_success_at": observer_last_success_at,
        "decisions": decisions,
        "maintainer_handoffs": maintainer_handoffs,
        "strong_retries_without_new_evidence": strong_retries,
        "duplicate_exact_context_reads": duplicate_reads,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observer", type=Path)
    parser.add_argument("--readiness", type=Path, action="append", default=[])
    parser.add_argument("--trace", type=Path, action="append", default=[])
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()

    observer = _load_json(args.observer) if args.observer else None
    snapshot = build_snapshot(
        observer=observer,
        observer_ref=f"file:{args.observer}" if args.observer else None,
        readiness=[(_load_json(path), f"file:{path}") for path in args.readiness],
        traces=[(_load_json(path), f"file:{path}") for path in args.trace],
    )
    output = json.dumps(snapshot, ensure_ascii=False, indent=2 if args.pretty else None)
    if args.json_out:
        args.json_out.write_text(output + "\n", encoding="utf-8")
    else:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
