"""
Regression tests for three Codex findings on feat/580-work-coordinator.

Expected state: RED at head 63ebe861dbc9f41d2b92a60834cfea182791ac65.
Do not mark passing until the corresponding production fix is applied and verified.

Findings:
  P1-skills  agent_skills/registry.py:41-46   docs-sync in work-coordinator allowlist
  P1-budget  agent_office/coordinator.py:180  omitted fingerprint bypasses strong-tier guard
  P2-provenance agent_office/coordinator.py:147 as_dict() leaks internal fields for user audience

No secrets, no live model calls, no unbounded input.
"""

from __future__ import annotations

import pytest

from agent_context import EvidenceRef, EvidenceVersion, TaskPacket, TaskScope
from agent_office.coordinator import (
    CoordinatorSoftContext,
    NoNewEvidenceError,
    WorkCoordinator,
    assert_reasoning_allowed,
)
from agent_office.dispatch_model import resolve_task_plan
from agent_office.task_state import AgentTaskEvidence
from agent_retrieval import RetrievalBundle, RetrievalHit
from agent_skills.registry import ROLE_SKILL_ALLOWLIST, SkillPolicyError, SkillRegistry
from trace_manager import ExecutionTrace


# ---------------------------------------------------------------------------
# Shared helpers (mirrors test_work_coordinator.py conventions)
# ---------------------------------------------------------------------------


class _FakeRetriever:
    def __init__(self):
        self.bundle = RetrievalBundle(
            status="hit",
            hits=(
                RetrievalHit(
                    source_type="memory",
                    ref="PR#580",
                    score=5.0,
                    text="compact coordinator evidence",
                    metadata={"kind": "project"},
                ),
            ),
            cache_status="partial",
            source_counts={"memory": 1},
            total_chars=28,
            saved_source_bytes=900,
            saved_input_tokens=150,
        )

    def retrieve(self, query, *, cache_lookup=None, now=None):
        return self.bundle


def _packet(*, role="Backend Engineer", budget="strong", skills="skills-v1"):
    return TaskPacket(
        scope=TaskScope(
            repository="maksimp6/Chat",
            work_item="issue#580",
            base_sha="base-1",
            head_sha="head-1",
            role=role,
            selected_skills=("github-ci-diagnosis",),
        ),
        evidence=EvidenceVersion(
            task="task-v1",
            ci="ci-v1",
            review="review-v1",
            trace="trace-v1",
            files="files-v1",
            skills=skills,
            policy="policy-v1",
        ),
        objective="Prepare implementation handoff",
        expected_deliverable="Focused backend PR",
        known_facts=("RAG is merged",),
        open_questions=("Which API hook owns the handoff?",),
        failed_attempts=("Do not reread the full repository",),
        evidence_refs=(EvidenceRef(kind="github_issue", ref="issue#580"),),
        changed_files=("agent_office/coordinator.py",),
        owner="Backend Engineer",
        budget_tier=budget,
        usage={"cheap_calls": 2, "normal_calls": 1, "strong_calls": 0},
        escalation_target="process-governor",
    )


def _plan(role="backend-engineer"):
    return resolve_task_plan(
        stage="implementation",
        role=role,
        backend="claude-direct",
    )


def _evidence(**overrides):
    values = {"workflow_status": "in_progress", "backend_triggered": True}
    values.update(overrides)
    return AgentTaskEvidence(**values)


# ---------------------------------------------------------------------------
# P1-skills: work-coordinator allowlist must not contain mutation-oriented skills
#
# AGENTS.md §Execution backends: "Custom agent profile uses gpt-5.4-nano and
# read/search only."  mcp_routes.py:202 calls skill_registry.load_many(skills,
# role=role) which enforces ROLE_SKILL_ALLOWLIST; any skill present in the
# allowlist for "work-coordinator" can be injected into /api/chat while the
# conversation's active tool categories remain available.
#
# Expected RED at 63ebe86: "docs-sync" is in ROLE_SKILL_ALLOWLIST["work-coordinator"].
# ---------------------------------------------------------------------------

# Skills that modify repository state.  Work Coordinator is read/search only.
_MUTATION_SKILLS = frozenset(
    {
        "docs-sync",          # edits documentation files
        "issue-to-pr",        # creates / pushes pull requests
        "release-readiness",  # manages release artifacts
        "cloudru-change",     # makes cloud infrastructure changes
    }
)


def test_work_coordinator_allowlist_excludes_mutation_skills():
    """
    P1-skills regression — head 63ebe86.

    ROLE_SKILL_ALLOWLIST["work-coordinator"] must not contain any mutation-oriented
    skill.  Currently "docs-sync" is present, allowing the chat endpoint to inject
    documentation-editing instructions into a read/search-only role.

    Expected RED: leaked == {"docs-sync"}.
    """
    coordinator_allowlist = ROLE_SKILL_ALLOWLIST.get("work-coordinator", set())
    leaked = coordinator_allowlist & _MUTATION_SKILLS
    assert not leaked, (
        f"work-coordinator ROLE_SKILL_ALLOWLIST contains mutation skill(s) "
        f"{sorted(leaked)!r}; coordinator is read/search-only per AGENTS.md and "
        "must not inject implementation instructions into /api/chat"
    )


def test_work_coordinator_cannot_load_docs_sync_skill(tmp_path):
    """
    P1-skills regression — chat boundary path — head 63ebe86.

    SkillRegistry.load("docs-sync", role="work-coordinator") must raise
    SkillPolicyError because work-coordinator is read/search-only.

    This exercises the exact production path at mcp_routes.py:202:
        skill_registry.load_many(requested_skills, role=role)

    Expected RED: load currently succeeds (no exception) because "docs-sync"
    is in the allowlist.
    """
    root = tmp_path / ".agents" / "skills"
    body = (
        "## Purpose\nSync docs.\n\n"
        "## Non-goals\nNone.\n\n"
        "## Inputs\n- target\n\n"
        "## Tools\n- write\n\n"
        "## Procedure\n1. Edit.\n\n"
        "## Approval boundaries\nRequires review.\n\n"
        "## Validation\nVerify.\n\n"
        "## Failure behavior\nFail closed.\n\n"
        "## Output\nUpdated docs.\n"
    )
    skill_dir = root / "docs-sync"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: docs-sync\ndescription: Synchronise documentation\n---\n" + body,
        encoding="utf-8",
    )

    registry = SkillRegistry(root)

    with pytest.raises(SkillPolicyError, match="work-coordinator"):
        registry.load("docs-sync", role="work-coordinator")


# ---------------------------------------------------------------------------
# P1-budget: strong reasoning without prior fingerprint must fail closed
#
# assert_reasoning_allowed lines 180-185: the unchanged-evidence check is
# conditioned on `previous_strong_evidence_fingerprint is not None`.  When the
# parameter is omitted (default None) the check is skipped unconditionally,
# allowing repeated identical strong calls to bypass the billing guard.
#
# Expected RED at 63ebe86: two successive calls with None fingerprint both
# return the fingerprint string without raising NoNewEvidenceError.
# ---------------------------------------------------------------------------


def test_strong_reasoning_guard_fails_closed_without_prior_fingerprint():
    """
    P1-budget regression — head 63ebe86.

    PR description: "repeated strong reasoning on the same exact task-context
    fingerprint fails closed."  The current guard is conditioned on the caller
    voluntarily supplying `previous_strong_evidence_fingerprint`; omitting it
    (passing None, the default) silently skips the unchanged-evidence check.

    A compliant implementation must treat omitted/None history as UNKNOWN, not as
    an established first call.  A stateless helper cannot infer prior use from the
    absence of a parameter; unknown history must fail closed on every observed call.

    OPEN: genuine first-use authorization requires a separately agreed trusted-history
    design (e.g. an explicit first-call sentinel derived from ExecutionTrace /
    TaskPacket.usage, not a process-global counter or wildcard string).  That design
    is not implemented here.

    Test contract:
    - Both calls with omitted prior fingerprint must raise NoNewEvidenceError.
    - Omitted/None history == unknown; unknown rejects even on the first observed call.

    Expected RED: both calls currently succeed (guard bypassed).
    """
    packet = _packet(budget="strong")

    # Both calls with prior fingerprint omitted must be rejected.
    # Unknown history is not an implicit free pass for the first observed call.
    for _ in range(2):
        with pytest.raises(NoNewEvidenceError, match="no new evidence"):
            assert_reasoning_allowed(
                packet,
                requested_tier="strong",
                # previous_strong_evidence_fingerprint intentionally omitted (None)
            )


def test_strong_reasoning_guard_treats_none_fingerprint_as_unknown_not_first_call():
    """
    P1-budget regression — head 63ebe86 (complementary assertion).

    When `previous_strong_evidence_fingerprint=None` is passed and the task-
    context fingerprint is deterministic and unchanged, the guard must not
    treat None as an unconditional free pass.  A compliant implementation
    requires the caller to explicitly declare first-call state (e.g. via a
    sentinel) rather than inferring it from the absence of the parameter.

    Expected RED: assert_reasoning_allowed succeeds for None on an identical
    packet context, demonstrating the bypass is available to any caller.
    """
    packet = _packet(budget="strong")
    fp = packet.cache_key()

    # Calling with None should not succeed when the fingerprint is known and
    # the task context is identical to a prior strong call.
    # A compliant guard rejects this path.  Current code does not → RED.
    with pytest.raises(NoNewEvidenceError, match="no new evidence"):
        assert_reasoning_allowed(
            packet,
            requested_tier="strong",
            previous_strong_evidence_fingerprint=None,
        )

    # Sanity: explicit matching fingerprint still raises (existing behavior preserved).
    with pytest.raises(NoNewEvidenceError, match="no new evidence"):
        assert_reasoning_allowed(
            packet,
            requested_tier="strong",
            previous_strong_evidence_fingerprint=fp,
        )


# ---------------------------------------------------------------------------
# P2-provenance: CoordinatorHandoff.as_dict() must be audience-aware for user
#
# CoordinatorHandoff.as_dict() lines 147-161 serialises all internal provenance
# fields unconditionally.  For audience="user" the payload is correctly filtered
# by _build_payload, but as_dict() still exposes evidence_fingerprint, head_sha,
# backend, stage, repository, work_item, owner_role, reasoning_tier, and
# recipient_role at the top level — precisely the provenance the audience contract
# is meant to omit.
#
# Expected RED at 63ebe86: all listed internal fields appear in the exported dict.
# ---------------------------------------------------------------------------

_USER_INTERNAL_FIELDS = frozenset(
    {
        "evidence_fingerprint",
        "head_sha",
        "backend",
        "stage",
        "repository",
        "work_item",
        "owner_role",
        "reasoning_tier",
    }
)


def test_user_handoff_as_dict_excludes_internal_provenance_at_top_level():
    """
    P2-provenance regression — head 63ebe86.

    For audience="user", CoordinatorHandoff.as_dict() must not expose internal
    git/trace provenance fields at the top level of the exported dict.  The
    payload branch in _build_payload already filters them; the public
    serialisation surface must be equally audience-aware.

    Expected RED: evidence_fingerprint, head_sha, backend, stage, repository,
    work_item, owner_role, reasoning_tier are all present in as_dict() output.
    """
    coordinator = WorkCoordinator(_FakeRetriever())
    handoff = coordinator.prepare_handoff(
        plan=_plan(),
        packet=_packet(),
        branch="feat/580-work-coordinator",
        task_evidence=_evidence(),
        trace=ExecutionTrace("trace-user-provenance-regression"),
        audience="user",
    )

    exported = handoff.as_dict()
    leaked = _USER_INTERNAL_FIELDS & exported.keys()

    assert not leaked, (
        f"CoordinatorHandoff.as_dict() exposes internal provenance for "
        f"audience='user': {sorted(leaked)!r}.  "
        "Provide an audience-aware serialisation surface or a separate "
        "user_safe_dict() method that omits these fields."
    )


def test_user_handoff_as_dict_safe_fields_are_present():
    """
    P2-provenance — positive contract (must remain green after the fix).

    Verifies that user-facing fields (objective, task_state, etc.) are still
    accessible in whatever audience-safe surface the fix provides.

    This test is written for the fixed API; it will also RED until the fix
    introduces the audience-safe serialisation method / attribute.
    """
    coordinator = WorkCoordinator(_FakeRetriever())
    handoff = coordinator.prepare_handoff(
        plan=_plan(),
        packet=_packet(),
        branch="feat/580-work-coordinator",
        task_evidence=_evidence(),
        trace=ExecutionTrace("trace-user-safe-fields"),
        audience="user",
        soft_context=CoordinatorSoftContext(
            urgency="high",
            latest_event="CI passed",
            blocker="pending review",
        ),
    )

    # Post-fix expectation: as_dict() (or an equivalent audience-safe method)
    # for user audience exposes only user-safe payload keys.
    exported = handoff.as_dict()

    # Internal fields must not be present.
    assert not (_USER_INTERNAL_FIELDS & exported.keys()), (
        "Internal provenance still present in user handoff serialisation"
    )

    # User-facing fields must still be accessible (through payload or top level).
    payload = exported.get("payload", exported)
    assert "objective" in payload
    assert "task_state" in payload
    assert payload.get("latest_event") == "CI passed"
