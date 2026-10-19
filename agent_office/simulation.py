"""Deterministic rehearsal of existing office models; never executes an agent.

Run on GitHub Actions: python -m agent_office.simulation --output office-simulation
All task evidence, deliverable URIs and review heads below are explicit fixtures.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from functools import partial
from pathlib import Path

from agent_office import dispatch_model as dispatch
from agent_office import task_state as states

FIXTURE_HEAD = "a" * 40
STALE_HEAD = "b" * 40
LIMITS = (
    "Fixture evidence only: no real agent or model was executed.",
    "No merge, deployment, device access, credentials or external API calls.",
    "can_merge is role capability, not owner authorization; OK is not merge permission.",
    "Review checks validate outcome/head, not reviewer identity or independence.",
    "This rehearsal does not establish APK fitness or product/device acceptance.",
)


@dataclass(frozen=True)
class Check:
    name: str
    expected: str
    actual: str

    @property
    def passed(self) -> bool:
        return self.actual == self.expected


@dataclass
class Report:
    source_sha: str
    run_id: str
    plans: list[dispatch.AgentTaskPlan]
    checks: list[Check]

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    def payload(self) -> dict[str, object]:
        return {
            "schema": 1,
            "mode": "fixture-rehearsal",
            "source_sha": self.source_sha,
            "run_id": self.run_id,
            "status": "SIMULATION_PASS" if self.passed else "SIMULATION_FAIL",
            "limits": list(LIMITS),
            "stages": [asdict(plan) for plan in self.plans],
            "checks": [dict(asdict(check), passed=check.passed) for check in self.checks],
        }


def state_check(name: str, evidence: states.AgentTaskEvidence, expected: str) -> Check:
    return Check(name, expected, states.derive_agent_task_state(evidence))


def rejection_check(name: str, operation: Callable[[], object], error: type[Exception]) -> Check:
    expected = "rejected:" + error.__name__
    try:
        operation()
    except error:
        return Check(name, expected, expected)
    return Check(name, expected, "accepted")


def lifecycle_checks() -> list[Check]:
    evidence = states.AgentTaskEvidence(dispatch_created=True)
    snapshots = [("queued", evidence)]
    evidence = replace(evidence, mention_delivered=True)
    snapshots.append(("acknowledged", evidence))
    evidence = replace(evidence, workflow_status="queued")
    snapshots.append(("dispatched", evidence))
    evidence = replace(evidence, workflow_status="in_progress", backend_triggered=True)
    snapshots.append(("working", evidence))
    evidence = replace(
        evidence,
        deliverable_refs=("fixture://office/result.txt",),
        validation_status="in_progress",
    )
    snapshots.append(("verifying", evidence))
    evidence = replace(evidence, validation_status="success", backend_status="completed")
    snapshots.append(("review", evidence))
    snapshots.append(("done", replace(evidence, review_complete=True)))
    return [state_check("lifecycle/" + name, item, name) for name, item in snapshots]


def evidence_checks() -> list[Check]:
    E = states.AgentTaskEvidence
    checks = [
        state_check("dispatch-created-is-not-work", E(dispatch_created=True), "queued"),
        state_check("mention-is-not-work", E(mention_delivered=True), "acknowledged"),
        state_check(
            "green-workflow-without-backend",
            E(workflow_status="completed", workflow_conclusion="success"),
            "queued",
        ),
        state_check("completed-without-artifact", E(backend_status="completed"), "failed"),
        state_check(
            "validated-artifact-without-backend-completion",
            E(
                deliverable_refs=("fixture://result",),
                validation_status="success",
                review_complete=True,
            ),
            "queued",
        ),
    ]
    for validation in (None, "in_progress", "failure"):
        evidence = E(review_complete=True, validation_status=validation)
        checks.append(
            rejection_check(
                "review-before-validation/" + str(validation),
                partial(states.derive_agent_task_state, evidence),
                states.AgentTaskStateError,
            )
        )
    checks.append(
        rejection_check(
            "unknown-backend-status",
            lambda: states.derive_agent_task_state(E(backend_status="green")),
            states.AgentTaskStateError,
        )
    )
    return checks


def terminal_checks() -> list[Check]:
    evidence = states.AgentTaskEvidence(
        cancelled=True, blocker="fixture blocker", backend_status="failed", stall_detected=True
    )
    checks = [state_check("terminal/cancelled", evidence, "cancelled")]
    evidence = replace(evidence, cancelled=False)
    checks.append(state_check("terminal/blocked", evidence, "blocked"))
    evidence = replace(evidence, blocker=None)
    checks.append(state_check("terminal/failed", evidence, "failed"))
    evidence = replace(evidence, backend_status=None)
    checks.append(state_check("terminal/stalled", evidence, "stalled"))
    return checks


def route_checks() -> list[Check]:
    routes = [
        ("implementation", None, "github-actions"),
        ("implementation", "test-engineer", "github-actions"),
        ("implementation", "backend-engineer", "copilot"),
        ("solution-review", "backend-engineer", "github-actions"),
        ("magic", "team-lead", "github-actions"),
        ("implementation", "wizard", "github-actions"),
        ("implementation", "backend-engineer", "unknown"),
    ]
    return [
        rejection_check(
            "reject-route/" + str(index),
            partial(dispatch.resolve_task_plan, stage, role, backend),
            dispatch.AgentDispatchError,
        )
        for index, (stage, role, backend) in enumerate(routes, 1)
    ]


def review_checks() -> list[Check]:
    artifacts = [
        ("missing", None),
        ("blocked", dispatch.SolutionReviewArtifact("BLOCKED", FIXTURE_HEAD, "team-lead")),
        (
            "changes-requested",
            dispatch.SolutionReviewArtifact("CHANGES_REQUESTED", FIXTURE_HEAD, "team-lead"),
        ),
        ("stale-head", dispatch.SolutionReviewArtifact("ACCEPTED", STALE_HEAD, "team-lead")),
    ]
    checks = [
        rejection_check(
            "solution-review/" + name,
            partial(dispatch.assert_solution_review_accepted_for_head, artifact, FIXTURE_HEAD),
            dispatch.SolutionReviewRequiredError,
        )
        for name, artifact in artifacts
    ]
    artifact = dispatch.SolutionReviewArtifact("ACCEPTED", FIXTURE_HEAD, "team-lead")
    dispatch.assert_solution_review_accepted_for_head(artifact, FIXTURE_HEAD)
    checks.append(Check("solution-review/current-head", "accepted", "accepted"))
    return checks


def simulate(source_sha: str = "unrecorded", run_id: str = "unrecorded") -> Report:
    roles = (
        "test-engineer",
        "team-lead",
        "backend-engineer",
        "test-engineer",
        "security-reviewer",
        "release-manager",
    )
    plans = [
        dispatch.resolve_task_plan(stage, role, "github-actions")
        for stage, role in zip(dispatch.CANONICAL_LIFECYCLE_STAGES, roles, strict=True)
    ]
    capabilities = [
        plans[0].can_write_contract_tests,
        plans[1].can_review_contract,
        plans[2].can_implement,
        not plans[3].can_implement,
        plans[4].can_review_solution,
        plans[5].can_merge,
    ]
    checks = [
        Check("stage-capabilities", "True", str(all(capabilities))),
        Check("no-approval-bypass", "False", str(any(p.can_bypass_approval for p in plans))),
        Check("implementation-cannot-merge", "False", str(plans[2].can_merge)),
    ]
    checks += lifecycle_checks() + evidence_checks() + terminal_checks()
    checks += route_checks() + review_checks()
    return Report(source_sha, run_id, plans, checks)


def markdown(report: Report) -> str:
    status = "SIMULATION_PASS" if report.passed else "SIMULATION_FAIL"
    rows = [
        "# Office simulation — " + status,
        "",
        "Deterministic fixture rehearsal of existing agent_office production functions.",
        "",
        "- Source SHA: `" + report.source_sha + "`",
        "- GitHub run: `" + report.run_id + "`",
        "- Checks passed: " + str(sum(c.passed for c in report.checks)),
        "- Checks failed: " + str(sum(not c.passed for c in report.checks)),
        "",
        "| Stage | Role | Backend |",
        "| --- | --- | --- |",
    ]
    rows += ["| " + p.stage + " | " + str(p.role) + " | " + p.backend + " |" for p in report.plans]
    rows += ["", "| Scenario | Expected | Actual | Result |", "| --- | --- | --- | --- |"]
    rows += [
        "| "
        + c.name
        + " | "
        + c.expected
        + " | "
        + c.actual
        + " | "
        + ("PASS" if c.passed else "FAIL")
        + " |"
        for c in report.checks
    ]
    rows += ["", "## Boundaries", ""] + ["- " + text for text in LIMITS]
    return "\n".join(rows) + "\n"


def write_report(report: Report, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "report.json").write_text(
        json.dumps(report.payload(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output / "report.md").write_text(markdown(report), encoding="utf-8")
    files = ("report.json", "report.md")
    checksums = [
        hashlib.sha256((output / name).read_bytes()).hexdigest() + "  " + name for name in files
    ]
    (output / "SHA256SUMS").write_text("\n".join(checksums) + "\n", encoding="ascii")


def unsafe_state(evidence: states.AgentTaskEvidence) -> str:
    """Deliberately broken function, activated only for the negative control."""
    return "done"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--negative-control", action="store_true")
    args = parser.parse_args()
    if args.negative_control:
        states.derive_agent_task_state = unsafe_state
    report = simulate(args.source_sha, args.run_id)
    write_report(report, args.output)
    print(markdown(report), end="")
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
