"""
RED contract tests for DevelopmentSession — issue #703.

These tests define the public API contract and MUST FAIL until
development_session.py is implemented. No live model/provider calls are
made; the coordinator uses a stub backend throughout.

Reuses #701/#702 session/invocation lifecycle semantics and #662 usage
accounting via the runtime_db fixture. No new state machine is introduced.
"""

import pytest


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def runtime_db(tmp_path, monkeypatch):
    import db
    import session_manager
    import invocation.manager as invocation_manager
    import runtime_migrations

    path = tmp_path / "runtime.db"
    monkeypatch.setattr(db, "DB_PATH", str(path))
    monkeypatch.setattr(session_manager, "get_conn", db.get_conn)
    monkeypatch.setattr(invocation_manager, "get_conn", db.get_conn)
    monkeypatch.setattr(runtime_migrations, "get_conn", db.get_conn)

    db.init_db()
    runtime_migrations.init_runtime_tables()
    return path


# ---------------------------------------------------------------------------
# Case 1 — DevelopmentSession identity is goal-centric; multiple Issues/PRs
#          may attach to one session.
# ---------------------------------------------------------------------------


def test_dev_session_identity_is_goal_not_work_item(runtime_db):
    from development_session import DevelopmentSession, DevelopmentSessionRegistry

    registry = DevelopmentSessionRegistry()
    session = registry.create(goal="implement-oauth-flow")

    session.attach_work_item("issue", "101")
    session.attach_work_item("issue", "102")
    session.attach_work_item("pr", "201")

    items = session.work_items()
    types_and_ids = {(i["type"], i["id"]) for i in items}
    assert ("issue", "101") in types_and_ids
    assert ("issue", "102") in types_and_ids
    assert ("pr", "201") in types_and_ids

    # Same goal → same session
    same = registry.get_by_goal("implement-oauth-flow")
    assert same is not None
    assert same.dev_session_id == session.dev_session_id

    # Different goal → different session
    other = registry.create(goal="fix-login-bug")
    assert other.dev_session_id != session.dev_session_id


# ---------------------------------------------------------------------------
# Case 2 — First material work creates exactly one primary provider session.
# ---------------------------------------------------------------------------


def test_first_material_work_creates_one_provider_session(runtime_db):
    from development_session import (
        DevelopmentSession,
        DevelopmentSessionRegistry,
        ProviderSessionCoordinator,
    )

    registry = DevelopmentSessionRegistry()
    coordinator = ProviderSessionCoordinator()
    session = registry.create(goal="add-rate-limiting")

    event = {"type": "work_started", "description": "implement token bucket"}
    result = coordinator.get_or_create_provider_session(session, "stub", event)

    assert result["action"] == "created"
    assert result["provider_session_id"]

    usage = session.get_usage()
    assert usage["creates"] == 1
    assert usage["resumes"] == 0


# ---------------------------------------------------------------------------
# Case 3 — Later material evidence resumes the same compatible provider
#          session (same ID, action == "resumed").
# ---------------------------------------------------------------------------


def test_later_material_event_resumes_same_provider_session(runtime_db):
    from development_session import DevelopmentSessionRegistry, ProviderSessionCoordinator

    registry = DevelopmentSessionRegistry()
    coordinator = ProviderSessionCoordinator()
    session = registry.create(goal="add-rate-limiting-resume")

    first = coordinator.get_or_create_provider_session(
        session, "stub", {"type": "work_started", "description": "first"}
    )
    assert first["action"] == "created"
    first_id = first["provider_session_id"]

    second = coordinator.get_or_create_provider_session(
        session, "stub", {"type": "ci_result", "status": "green"}
    )
    assert second["action"] == "resumed"
    assert second["provider_session_id"] == first_id

    usage = session.get_usage()
    assert usage["creates"] == 1
    assert usage["resumes"] == 1


# ---------------------------------------------------------------------------
# Case 4 — Duplicate evidence causes zero additional provider/model call.
# ---------------------------------------------------------------------------


def test_duplicate_event_causes_no_second_model_call(runtime_db):
    from development_session import DevelopmentSessionRegistry, ProviderSessionCoordinator

    registry = DevelopmentSessionRegistry()
    coordinator = ProviderSessionCoordinator()
    session = registry.create(goal="dedup-test")

    event = {"type": "ci_result", "run_id": "run-42", "status": "green"}
    first_material = session.record_event(event)
    assert first_material is True

    # Identical event must be recognized as a duplicate
    second_material = session.record_event(event)
    assert second_material is False

    # Coordinator must not dispatch a second call for the duplicate
    usage_before = session.get_usage()
    coordinator.get_or_create_provider_session(session, "stub", event)
    usage_after = session.get_usage()

    # No extra create or resume when the event is not material
    assert usage_after["creates"] == usage_before["creates"]
    assert usage_after["resumes"] == usage_before["resumes"]


# ---------------------------------------------------------------------------
# Case 5 — Role switch reuses the session, increments role_epoch, and
#          replaces capabilities atomically.
# ---------------------------------------------------------------------------


def test_role_switch_increments_epoch_and_replaces_capabilities(runtime_db):
    from development_session import DevelopmentSessionRegistry, ProviderSessionCoordinator

    registry = DevelopmentSessionRegistry()
    coordinator = ProviderSessionCoordinator()
    session = registry.create(goal="role-switch-test")

    coordinator.get_or_create_provider_session(
        session, "stub", {"type": "work_started", "description": "start"}
    )
    provider_id_before = coordinator.active_provider_session_id(session)
    epoch_before = session.role_epoch

    session.switch_role("backend-engineer", frozenset(["filesystem", "git"]))

    provider_id_after = coordinator.active_provider_session_id(session)
    epoch_after = session.role_epoch

    # Same provider session — reused, not recreated
    assert provider_id_after == provider_id_before

    # Epoch incremented by exactly 1
    assert epoch_after == epoch_before + 1

    # New capabilities are active
    assert session.capabilities == frozenset(["filesystem", "git"])


# ---------------------------------------------------------------------------
# Case 6 — Forbidden capabilities cannot leak from a prior role.
# ---------------------------------------------------------------------------


def test_forbidden_capabilities_do_not_leak_after_role_switch(runtime_db):
    from development_session import DevelopmentSessionRegistry

    registry = DevelopmentSessionRegistry()
    session = registry.create(goal="capability-leak-test")

    # Coordinator role: broad capabilities including "merge"
    session.switch_role("coordinator", frozenset(["read", "write", "merge", "deploy"]))
    assert "merge" in session.capabilities
    assert "deploy" in session.capabilities

    # Switch to test-engineer: restricted set only
    session.switch_role("test-engineer", frozenset(["read", "write"]))

    assert "merge" not in session.capabilities
    assert "deploy" not in session.capabilities
    assert session.capabilities == frozenset(["read", "write"])


# ---------------------------------------------------------------------------
# Case 7 — Independent review creates a fresh isolated provider session.
# ---------------------------------------------------------------------------


def test_independent_review_creates_fresh_provider_session(runtime_db):
    from development_session import DevelopmentSessionRegistry, ProviderSessionCoordinator

    registry = DevelopmentSessionRegistry()
    coordinator = ProviderSessionCoordinator()
    session = registry.create(goal="independent-review-test")

    primary = coordinator.get_or_create_provider_session(
        session, "stub", {"type": "work_started", "description": "impl"}
    )
    primary_id = primary["provider_session_id"]

    review = coordinator.create_independent_review_session(
        session, "stub", {"evidence": "diff-abc", "context": "pr-201"}
    )

    assert review["action"] == "created"
    assert review["isolated"] is True
    assert review["provider_session_id"] != primary_id


# ---------------------------------------------------------------------------
# Case 8 — Backend change creates a new provider session while durable
#          DevelopmentSession state is preserved.
# ---------------------------------------------------------------------------


def test_backend_change_creates_new_provider_session_preserves_dev_state(runtime_db):
    from development_session import DevelopmentSessionRegistry, ProviderSessionCoordinator

    registry = DevelopmentSessionRegistry()
    coordinator = ProviderSessionCoordinator()
    session = registry.create(goal="backend-change-test")

    session.attach_work_item("issue", "500")

    first = coordinator.get_or_create_provider_session(
        session, "backend-a", {"type": "work_started", "description": "first impl"}
    )
    first_id = first["provider_session_id"]

    # Switch backend — must create a new provider session
    second = coordinator.get_or_create_provider_session(
        session, "backend-b", {"type": "work_started", "description": "continued on new backend"}
    )

    assert second["action"] == "created"
    assert second["provider_session_id"] != first_id

    # DevelopmentSession identity and work items are intact
    assert session.dev_session_id == registry.get_by_goal("backend-change-test").dev_session_id
    items = {(i["type"], i["id"]) for i in session.work_items()}
    assert ("issue", "500") in items


# ---------------------------------------------------------------------------
# Case 9 — Provider-native hidden context is never modeled as portable state.
# ---------------------------------------------------------------------------


def test_provider_hidden_context_is_not_portable(runtime_db):
    from development_session import DevelopmentSessionRegistry, ProviderSessionCoordinator

    registry = DevelopmentSessionRegistry()
    coordinator = ProviderSessionCoordinator()
    session = registry.create(goal="no-hidden-context-test")

    coordinator.get_or_create_provider_session(
        session, "stub", {"type": "work_started", "description": "impl"}
    )

    serialized = session.to_dict()

    # DevelopmentSession state must not contain a field that claims to export
    # provider-native model context (weights, KV cache, etc.)
    for forbidden_key in ("provider_context", "model_context", "kv_cache", "hidden_state"):
        assert forbidden_key not in serialized, (
            f"Portable provider hidden context found in serialized state: {forbidden_key!r}"
        )

    # The coordinator itself must not expose an export method
    assert not hasattr(coordinator, "export_provider_context"), (
        "ProviderSessionCoordinator must not expose export_provider_context()"
    )
    assert not hasattr(coordinator, "checkpoint_provider_context"), (
        "ProviderSessionCoordinator must not expose checkpoint_provider_context()"
    )


# ---------------------------------------------------------------------------
# Case 10 — Mission compiler separates current authoritative instruction from
#           superseded thread chatter while preserving provenance.
# ---------------------------------------------------------------------------


def test_mission_compiler_excludes_superseded_chatter(runtime_db):
    from development_session import DevelopmentSessionRegistry

    registry = DevelopmentSessionRegistry()
    session = registry.create(goal="mission-compiler-test")

    # Record a superseded discussion event (old plan that was rejected)
    session.record_event({
        "type": "thread_comment",
        "author": "user",
        "body": "let's try approach A",
        "superseded": True,
    })

    # Record a decision event (authoritative current state)
    session.record_event({
        "type": "decision",
        "body": "approach B chosen — approach A rejected due to performance",
        "authoritative": True,
    })

    mission = session.compile_mission()

    # Active instructions must not contain the superseded comment body
    instructions_text = " ".join(str(i) for i in mission["instructions"])
    assert "approach A" not in instructions_text, (
        "Superseded chatter must not appear in active mission instructions"
    )

    # Provenance must retain it for audit
    provenance_text = " ".join(str(p) for p in mission["provenance"])
    assert "approach A" in provenance_text, (
        "Superseded chatter must be retained in mission provenance"
    )

    # Decision is in active instructions
    assert "approach B" in instructions_text


# ---------------------------------------------------------------------------
# Case 11 — After initial mission, only material deltas are delivered.
# ---------------------------------------------------------------------------


def test_only_material_deltas_delivered_after_initial_mission(runtime_db):
    from development_session import DevelopmentSessionRegistry

    registry = DevelopmentSessionRegistry()
    session = registry.create(goal="delta-delivery-test")

    session.record_event({"type": "work_started", "description": "initial scope"})

    # First compile — full mission
    first_mission = session.compile_mission()
    assert first_mission["delta_only"] is False

    # No new events since last compile
    second_mission = session.compile_mission()
    assert second_mission["delta_only"] is True
    assert second_mission["instructions"] == [], (
        "No new instructions must be delivered when there are no material deltas"
    )

    # New material event → delta delivered
    session.record_event({"type": "ci_result", "run_id": "run-99", "status": "failed"})
    third_mission = session.compile_mission()
    assert third_mission["delta_only"] is True
    assert len(third_mission["instructions"]) > 0


# ---------------------------------------------------------------------------
# Case 12 — Owner interruption is limited to explicit approval, budget/authority
#           escalation, genuine choice, unrecoverable blocker, or terminal result.
# ---------------------------------------------------------------------------


def test_owner_interruption_only_at_explicit_boundaries(runtime_db):
    from development_session import DevelopmentSessionRegistry

    registry = DevelopmentSessionRegistry()
    session = registry.create(goal="owner-interrupt-test")

    # Routine progress events must NOT require owner interruption
    for routine_event in [
        {"type": "ci_result", "status": "green"},
        {"type": "commit_pushed", "sha": "abc123"},
        {"type": "pr_opened", "pr_id": "301"},
        {"type": "review_requested"},
        {"type": "test_passed"},
    ]:
        session.record_event(routine_event)
        assert session.requires_owner_interruption() is False, (
            f"Routine event {routine_event['type']!r} must not trigger owner interruption"
        )

    # Explicit owner-boundary events MUST require interruption
    for boundary_event in [
        {"type": "approval_required", "action": "merge-to-main"},
        {"type": "budget_exceeded", "limit_usd": 10},
        {"type": "product_choice", "options": ["option-a", "option-b"]},
        {"type": "unrecoverable_blocker", "reason": "missing credentials"},
        {"type": "terminal", "outcome": "completed"},
    ]:
        session_b = registry.create(goal=f"owner-test-{boundary_event['type']}")
        session_b.record_event(boundary_event)
        assert session_b.requires_owner_interruption() is True, (
            f"Boundary event {boundary_event['type']!r} must trigger owner interruption"
        )


# ---------------------------------------------------------------------------
# Case 13 — Usage metadata distinguishes session create vs resume and is
#           correlatable to #662 accounting.
# ---------------------------------------------------------------------------


def test_usage_metadata_distinguishes_create_and_resume_for_accounting(runtime_db):
    from development_session import DevelopmentSessionRegistry, ProviderSessionCoordinator

    registry = DevelopmentSessionRegistry()
    coordinator = ProviderSessionCoordinator()
    session = registry.create(goal="usage-accounting-test")

    # 1 create + 3 resumes
    coordinator.get_or_create_provider_session(
        session, "stub", {"type": "work_started", "description": "start"}
    )
    for i in range(3):
        coordinator.get_or_create_provider_session(
            session, "stub", {"type": "ci_result", "run_id": f"run-{i}", "status": "green"}
        )

    usage = session.get_usage()
    assert usage["creates"] == 1
    assert usage["resumes"] == 3

    # Usage record must include dev_session_id for #662 correlation
    assert "dev_session_id" in usage
    assert usage["dev_session_id"] == session.dev_session_id


# ---------------------------------------------------------------------------
# Case 14 — Restart/replay restores registry mapping without duplicate paid
#           dispatch.
# ---------------------------------------------------------------------------


def test_restart_restores_registry_without_duplicate_dispatch(runtime_db):
    from development_session import DevelopmentSessionRegistry, ProviderSessionCoordinator

    registry = DevelopmentSessionRegistry()
    coordinator = ProviderSessionCoordinator()
    session = registry.create(goal="restart-recovery-test")
    session.attach_work_item("issue", "700")

    coordinator.get_or_create_provider_session(
        session, "stub", {"type": "work_started", "description": "pre-restart work"}
    )
    original_id = session.dev_session_id
    usage_before = session.get_usage()

    # Simulate restart: create a new registry and restore
    new_registry = DevelopmentSessionRegistry()
    restored = new_registry.restore(original_id)

    assert restored is not None
    assert restored.dev_session_id == original_id
    assert restored.goal == "restart-recovery-test"
    items = {(i["type"], i["id"]) for i in restored.work_items()}
    assert ("issue", "700") in items

    # get_by_goal must resolve back to the same session after restore
    by_goal = new_registry.get_by_goal("restart-recovery-test")
    assert by_goal is not None
    assert by_goal.dev_session_id == original_id

    # No additional model calls created during restore
    usage_after = restored.get_usage()
    assert usage_after["creates"] == usage_before["creates"]
    assert usage_after["resumes"] == usage_before["resumes"]
