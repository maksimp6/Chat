from types import MappingProxyType

import pytest

from agent_context import EvidenceRef, EvidenceVersion, TaskPacket, TaskScope
from agent_office.coordinator import (
    CoordinatorHandoff,
    CoordinatorPolicyError,
    CoordinatorScopeError,
    CoordinatorSoftContext,
    NoNewEvidenceError,
    WorkCoordinator,
    assert_reasoning_allowed,
)
from agent_office.dispatch_model import resolve_task_plan
from agent_office.task_state import AgentTaskEvidence
from agent_retrieval import RetrievalBundle, RetrievalHit
from trace_manager import ExecutionTrace


class FakeRetriever:
    def __init__(self, bundle=None):
        self.bundle = bundle or RetrievalBundle(
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
        self.queries = []

    def retrieve(self, query, *, cache_lookup=None, now=None):
        self.queries.append((query, cache_lookup, now))
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


def _plan():
    return resolve_task_plan(
        stage="implementation",
        role="backend-engineer",
        backend="claude-direct",
    )


def _evidence(**overrides):
    values = {
        "workflow_status": "in_progress",
        "backend_triggered": True,
    }
    values.update(overrides)
    return AgentTaskEvidence(**values)


def test_specialist_handoff_reuses_existing_contracts_and_records_trace():
    retriever = FakeRetriever()
    trace = ExecutionTrace("trace-coordinator")
    coordinator = WorkCoordinator(retriever)

    handoff = coordinator.prepare_handoff(
        plan=_plan(),
        packet=_packet(),
        branch="feat/580-work-coordinator",
        task_evidence=_evidence(),
        trace=trace,
        audience="specialist",
        requested_reasoning_tier="normal",
        now=100,
    )

    query, cache_lookup, now = retriever.queries[0]
    assert query.repository == "maksimp6/Chat"
    assert query.work_item == "issue#580"
    assert query.branch == "feat/580-work-coordinator"
    assert query.head_sha == "head-1"
    assert query.role == "backend-engineer"
    assert query.skills_version == "skills-v1"
    assert query.selected_skills == ("github-ci-diagnosis",)
    assert cache_lookup is None and now == 100

    payload = handoff.as_dict()["payload"]
    assert handoff.owner_role == "backend-engineer"
    assert handoff.recipient_role == "backend-engineer"
    assert handoff.task_state == "working"
    assert payload["expected_deliverable"] == "Focused backend PR"
    assert payload["do_not_repeat"] == ["Do not reread the full repository"]
    assert payload["context"][0]["ref"] == "PR#580"
    assert payload["usage"]["saved_input_tokens"] == 150

    types = [event["type"] for event in trace.trace["events"]]
    assert "hybrid_retrieval" in types
    assert "coordinator_handoff_prepared" in types
    assert trace.trace["coordinator_handoffs"][0]["head_sha"] == "head-1"
    assert handoff.repository == "maksimp6/Chat"
    assert handoff.work_item == "issue#580"
    assert handoff.head_sha == "head-1"


@pytest.mark.parametrize(
    ("audience", "recipient_role"),
    [
        ("maintainer", "release-manager"),
        ("observer", "operations-observer"),
        ("governor", "process-governor"),
        ("user", "user"),
    ],
)
def test_recipient_specific_handoffs_do_not_dump_specialist_context(
    audience,
    recipient_role,
):
    coordinator = WorkCoordinator(FakeRetriever())
    handoff = coordinator.prepare_handoff(
        plan=_plan(),
        packet=_packet(),
        branch="feat/580-work-coordinator",
        task_evidence=_evidence(),
        trace=ExecutionTrace(f"trace-{audience}"),
        audience=audience,
        soft_context=CoordinatorSoftContext(
            urgency="high",
            confidence=0.75,
            latest_event="CI passed",
            blocker="waiting for review" if audience != "user" else None,
        ),
    )

    payload = handoff.as_dict()["payload"]
    assert handoff.recipient_role == recipient_role
    assert "context" not in payload
    if audience in {"maintainer", "observer"}:
        assert payload["context_refs"][0]["ref"] == "PR#580"
    if audience == "user":
        assert "context_refs" not in payload
        assert "evidence_refs" not in payload
        assert "repository" not in payload
        assert "work_item" not in payload
        assert "branch" not in payload
        assert "base_sha" not in payload
        assert "head_sha" not in payload
        assert "reasoning_tier" not in payload
        assert payload["latest_event"] == "CI passed"
        assert payload["next_meaningful_step"] == "Focused backend PR"


def test_user_handoff_uses_canonical_blocked_state_without_internal_context():
    handoff = WorkCoordinator(FakeRetriever()).prepare_handoff(
        plan=_plan(),
        packet=_packet(),
        branch="feat/580-work-coordinator",
        task_evidence=_evidence(blocker="review required"),
        trace=ExecutionTrace("trace-user-blocked"),
        audience="user",
        soft_context=CoordinatorSoftContext(blocker="review required"),
    )

    payload = handoff.as_dict()["payload"]
    assert handoff.task_state == "blocked"
    assert payload["blocker"] == "review required"
    assert "do_not_repeat" not in payload
    assert "open_questions" not in payload


def test_role_mismatch_and_missing_branch_fail_closed():
    coordinator = WorkCoordinator(FakeRetriever())
    with pytest.raises(CoordinatorScopeError, match="does not match"):
        coordinator.prepare_handoff(
            plan=_plan(),
            packet=_packet(role="Frontend Engineer"),
            branch="feat/580",
            task_evidence=_evidence(),
            trace=ExecutionTrace("trace-role-mismatch"),
        )
    with pytest.raises(CoordinatorScopeError, match="branch"):
        coordinator.prepare_handoff(
            plan=_plan(),
            packet=_packet(),
            branch="",
            task_evidence=_evidence(),
            trace=ExecutionTrace("trace-branch-missing"),
        )


def test_selected_skills_require_versioned_evidence():
    coordinator = WorkCoordinator(FakeRetriever())
    with pytest.raises(CoordinatorScopeError, match="skill evidence"):
        coordinator.prepare_handoff(
            plan=_plan(),
            packet=_packet(skills=""),
            branch="feat/580",
            task_evidence=_evidence(),
            trace=ExecutionTrace("trace-skills-missing"),
        )


def test_reasoning_budget_rejects_over_budget_and_repeated_strong_call():
    cheap = _packet(budget="cheap")
    with pytest.raises(CoordinatorPolicyError, match="exceeds"):
        assert_reasoning_allowed(cheap, requested_tier="strong")

    packet = _packet(budget="strong")
    fingerprint = packet.cache_key()
    with pytest.raises(NoNewEvidenceError, match="no new evidence"):
        assert_reasoning_allowed(
            packet,
            requested_tier="strong",
            previous_strong_evidence_fingerprint=fingerprint,
        )

    changed = _packet(budget="strong")
    object.__setattr__(
        changed,
        "evidence",
        EvidenceVersion(task="task-v2", skills="skills-v1"),
    )
    assert (
        assert_reasoning_allowed(
            changed,
            requested_tier="strong",
            previous_strong_evidence_fingerprint=fingerprint,
        )
        != fingerprint
    )


def test_head_change_counts_as_new_evidence_for_strong_reasoning():
    packet = _packet(budget="strong")
    fingerprint = packet.cache_key()
    changed_scope = TaskScope(
        repository=packet.scope.repository,
        work_item=packet.scope.work_item,
        base_sha=packet.scope.base_sha,
        head_sha="head-2",
        role=packet.scope.role,
        selected_skills=packet.scope.selected_skills,
    )
    object.__setattr__(packet, "scope", changed_scope)

    assert (
        assert_reasoning_allowed(
            packet,
            requested_tier="strong",
            previous_strong_evidence_fingerprint=fingerprint,
        )
        != fingerprint
    )


def test_coordinator_handoff_contract_validation_fails_closed():
    with pytest.raises(CoordinatorPolicyError, match="audience"):
        CoordinatorHandoff(
            audience="mystery",
            recipient_role="backend-engineer",
            owner_role="backend-engineer",
            stage="implementation",
            backend="codex",
            task_state="working",
            reasoning_tier="cheap",
            evidence_fingerprint="fp",
            repository="maksimp6/Chat",
            work_item="issue#580",
            head_sha="head-1",
        )

    with pytest.raises(CoordinatorPolicyError, match="recipient_role"):
        CoordinatorHandoff(
            audience="specialist",
            recipient_role="",
            owner_role="backend-engineer",
            stage="implementation",
            backend="codex",
            task_state="working",
            reasoning_tier="cheap",
            evidence_fingerprint="fp",
            repository="maksimp6/Chat",
            work_item="issue#580",
            head_sha="head-1",
        )

    with pytest.raises(CoordinatorPolicyError, match="reasoning tier"):
        CoordinatorHandoff(
            audience="specialist",
            recipient_role="backend-engineer",
            owner_role="backend-engineer",
            stage="implementation",
            backend="codex",
            task_state="working",
            reasoning_tier="magic",
            evidence_fingerprint="fp",
            repository="maksimp6/Chat",
            work_item="issue#580",
            head_sha="head-1",
        )


def test_prepare_handoff_rejects_invalid_audience_and_missing_owner():
    coordinator = WorkCoordinator(FakeRetriever())
    with pytest.raises(CoordinatorPolicyError, match="audience"):
        coordinator.prepare_handoff(
            plan=_plan(),
            packet=_packet(),
            branch="feat/580",
            task_evidence=_evidence(),
            trace=ExecutionTrace("trace-bad-audience"),
            audience="mystery",
        )

    ownerless = resolve_task_plan(
        stage="verification",
        role=None,
        backend="claude-direct",
    )
    packet = _packet()
    object.__setattr__(packet.scope, "role", None)
    with pytest.raises(CoordinatorScopeError, match="already-selected owner"):
        coordinator.prepare_handoff(
            plan=ownerless,
            packet=packet,
            branch="feat/580",
            task_evidence=_evidence(),
            trace=ExecutionTrace("trace-no-owner"),
        )


def test_reasoning_and_soft_context_validation_fail_closed():
    with pytest.raises(CoordinatorPolicyError, match="unsupported reasoning"):
        assert_reasoning_allowed(_packet(), requested_tier="magic")
    with pytest.raises(CoordinatorPolicyError, match="budget"):
        assert_reasoning_allowed(_packet(budget="mystery"), requested_tier="cheap")
    with pytest.raises(CoordinatorPolicyError, match="confidence"):
        CoordinatorSoftContext(confidence=1.5)
    with pytest.raises(CoordinatorPolicyError, match="urgency"):
        CoordinatorSoftContext(urgency="panic")


def test_usage_counters_must_be_nonnegative_integers():
    coordinator = WorkCoordinator(FakeRetriever())

    negative = _packet()
    object.__setattr__(
        negative,
        "usage",
        MappingProxyType({"cheap_calls": -1, "normal_calls": 0, "strong_calls": 0}),
    )
    with pytest.raises(CoordinatorPolicyError, match="non-negative"):
        coordinator.prepare_handoff(
            plan=_plan(),
            packet=negative,
            branch="feat/580",
            task_evidence=_evidence(),
            trace=ExecutionTrace("trace-negative-usage"),
        )

    non_integer = _packet()
    object.__setattr__(
        non_integer,
        "usage",
        MappingProxyType(
            {"cheap_calls": "not-a-number", "normal_calls": 0, "strong_calls": 0}
        ),
    )
    with pytest.raises(CoordinatorPolicyError, match="integer"):
        coordinator.prepare_handoff(
            plan=_plan(),
            packet=non_integer,
            branch="feat/580",
            task_evidence=_evidence(),
            trace=ExecutionTrace("trace-invalid-usage"),
        )


def test_handoff_payload_is_immutable_but_exports_independent_copy():
    handoff = WorkCoordinator(FakeRetriever()).prepare_handoff(
        plan=_plan(),
        packet=_packet(),
        branch="feat/580",
        task_evidence=_evidence(),
        trace=ExecutionTrace("trace-immutable"),
    )

    with pytest.raises(TypeError):
        handoff.payload["usage"]["cheap_calls"] = 99

    exported = handoff.as_dict()
    exported["payload"]["usage"]["cheap_calls"] = 99
    assert handoff.as_dict()["payload"]["usage"]["cheap_calls"] == 2


def test_trace_records_metadata_not_handoff_context_body():
    trace = ExecutionTrace("trace-no-body")
    handoff = WorkCoordinator(FakeRetriever()).prepare_handoff(
        plan=_plan(),
        packet=_packet(),
        branch="feat/580",
        task_evidence=_evidence(),
        trace=trace,
    )
    assert handoff.payload["context"][0]["text"] == "compact coordinator evidence"

    serialized = str(trace.finalize())
    assert "compact coordinator evidence" not in serialized
    assert "Do not reread the full repository" not in serialized
