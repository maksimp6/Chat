"""RED contract tests for the browser planner/supervisor adapter.

These tests define the behavioral contract for the planner adapter layer that
sits atop BrowserSynthesisResult.  They are intentionally RED: the adapter
module (browser.planner) does not exist yet and must not be added in this PR.

Lifecycle stage: contract
Task ID: browser-planner-contract-v1
Issue: #661
"""

from __future__ import annotations

import json
from typing import Any, Mapping

import pytest

# ---------------------------------------------------------------------------
# Contract import – RED: browser.planner does not exist yet.
# Every test that reaches this import will fail with ImportError until the
# Backend Engineer creates the module in the implementation stage.
# ---------------------------------------------------------------------------
from browser.planner import (  # noqa: F401  (expected ImportError)
    BrowserPlannerAdapter,
    PlannerDecision,
    PlannerDecisionKind,
    PlannerPayload,
    PlannerTierPolicy,
    build_planner_payload,
    run_planner,
)

# ---------------------------------------------------------------------------
# Helpers – these use only already-shipped modules so they never import-fail.
# ---------------------------------------------------------------------------
from browser.orchestration import (
    BrowserDagBudget,
    BrowserDagResult,
    BrowserRoleDag,
    BrowserRoleTask,
    BrowserTaskResult,
)
from browser.pipeline import (
    BrowserSynthesisPolicy,
    BrowserSynthesisResult,
    EscalationDecision,
    synthesize_browser_dag,
)
from browser.semantic import MergedEvidence, SemanticSnapshot


def _make_snapshot(url: str = "https://example.com", *, facts: dict[str, Any] | None = None) -> SemanticSnapshot:
    return SemanticSnapshot(url=url, title="Test", version="v1", facts=facts or {})


def _deterministic_synthesis() -> BrowserSynthesisResult:
    """A synthesis result with no conflicts, no failures, no truncation."""
    evidence = MergedEvidence(facts={"item": "widget"}, conflicts={}, sources={"item": ("t1",)})
    escalation = EscalationDecision(required=False, suggested_tier="deterministic", reasons=())
    return BrowserSynthesisResult(
        status="succeeded",
        task_statuses={"t1": "succeeded"},
        snapshots={"t1": _make_snapshot(facts={"item": "widget"})},
        diffs={},
        evidence=evidence,
        consumed_tokens=10,
        escalation=escalation,
    )


def _cheap_synthesis() -> BrowserSynthesisResult:
    """A synthesis result that is truncated – escalates to cheap."""
    snap = SemanticSnapshot(url="https://example.com", title="T", version="v1", truncated=True)
    evidence = MergedEvidence(facts={}, conflicts={}, sources={})
    escalation = EscalationDecision(
        required=True, suggested_tier="cheap", reasons=("truncated_state:t1",)
    )
    return BrowserSynthesisResult(
        status="succeeded",
        task_statuses={"t1": "succeeded"},
        snapshots={"t1": snap},
        diffs={},
        evidence=evidence,
        consumed_tokens=10,
        escalation=escalation,
    )


def _strong_synthesis() -> BrowserSynthesisResult:
    """A synthesis result with evidence conflicts – escalates to strong."""
    evidence = MergedEvidence(
        facts={},
        conflicts={"price": ({"value": 10, "confidence": 0.9, "sources": ["a"]}, {"value": 12, "confidence": 0.8, "sources": ["b"]})},
        sources={},
    )
    escalation = EscalationDecision(
        required=True, suggested_tier="strong", reasons=("evidence_conflicts:price",)
    )
    return BrowserSynthesisResult(
        status="succeeded",
        task_statuses={"t1": "succeeded"},
        snapshots={"t1": _make_snapshot()},
        diffs={},
        evidence=evidence,
        consumed_tokens=10,
        escalation=escalation,
    )


def _raw_browser_payload_bytes() -> int:
    """Approximate size of a raw DOM-style browser payload."""
    raw = {
        "url": "https://example.com",
        "title": "Test",
        "html": "<html>" + "x" * 50_000 + "</html>",
        "screenshot_base64": "A" * 20_000,
        "accessibility_tree": [{"role": "button", "name": f"btn{i}", "id": str(i)} for i in range(200)],
    }
    return len(json.dumps(raw))


# ---------------------------------------------------------------------------
# CONTRACT 1 – Deterministic synthesis causes zero planner/model calls.
# ---------------------------------------------------------------------------


class TestDeterministicZeroModelCalls:
    def test_deterministic_tier_calls_no_model(self) -> None:
        """PlannerAdapter must not invoke any model backend for deterministic synthesis."""
        model_calls: list[str] = []

        def spy_backend(payload: Any) -> Any:
            model_calls.append("called")
            return {"decision": "next_action", "rationale": "spy"}

        synthesis = _deterministic_synthesis()
        adapter = BrowserPlannerAdapter(backend=spy_backend, policy=PlannerTierPolicy())
        adapter.plan(synthesis)
        assert model_calls == [], (
            "deterministic synthesis must cause zero model/backend calls; "
            f"got {len(model_calls)} call(s)"
        )

    def test_deterministic_tier_returns_typed_decision(self) -> None:
        synthesis = _deterministic_synthesis()
        adapter = BrowserPlannerAdapter(backend=None, policy=PlannerTierPolicy())
        decision = adapter.plan(synthesis)
        assert isinstance(decision, PlannerDecision)
        assert decision.kind in {PlannerDecisionKind.NEXT_ACTION, PlannerDecisionKind.STOP, PlannerDecisionKind.GATHER_MORE_EVIDENCE}


# ---------------------------------------------------------------------------
# CONTRACT 2 – Cheap tier selects bounded lightweight path.
# ---------------------------------------------------------------------------


class TestCheapTierBoundedPayload:
    def test_cheap_tier_is_selected_for_cheap_synthesis(self) -> None:
        selected_tiers: list[str] = []

        def spy_backend(payload: PlannerPayload) -> Any:
            selected_tiers.append(payload.tier)
            return {"decision": "next_action", "rationale": "ok"}

        synthesis = _cheap_synthesis()
        adapter = BrowserPlannerAdapter(backend=spy_backend, policy=PlannerTierPolicy())
        adapter.plan(synthesis)
        assert selected_tiers == ["cheap"], f"expected cheap tier, got {selected_tiers}"

    def test_cheap_tier_payload_size_bounded(self) -> None:
        sizes: list[int] = []

        def spy_backend(payload: PlannerPayload) -> Any:
            sizes.append(payload.serialized_size_bytes)
            return {"decision": "next_action", "rationale": "ok"}

        synthesis = _cheap_synthesis()
        adapter = BrowserPlannerAdapter(backend=spy_backend, policy=PlannerTierPolicy(max_payload_bytes=32_000))
        adapter.plan(synthesis)
        assert sizes, "backend was not called for cheap tier"
        assert sizes[0] <= 32_000, (
            f"cheap tier payload {sizes[0]} bytes exceeds 32 000-byte bound"
        )


# ---------------------------------------------------------------------------
# CONTRACT 3 – Strong tier only when synthesis explicitly suggests "strong".
# ---------------------------------------------------------------------------


class TestStrongTierOnlyOnEvidence:
    def test_strong_tier_invoked_for_strong_synthesis(self) -> None:
        selected_tiers: list[str] = []

        def spy_backend(payload: PlannerPayload) -> Any:
            selected_tiers.append(payload.tier)
            return {"decision": "next_action", "rationale": "ok"}

        synthesis = _strong_synthesis()
        adapter = BrowserPlannerAdapter(backend=spy_backend, policy=PlannerTierPolicy())
        adapter.plan(synthesis)
        assert selected_tiers == ["strong"], f"expected strong tier, got {selected_tiers}"

    def test_strong_tier_not_invoked_for_cheap_synthesis(self) -> None:
        selected_tiers: list[str] = []

        def spy_backend(payload: PlannerPayload) -> Any:
            selected_tiers.append(payload.tier)
            return {"decision": "next_action", "rationale": "ok"}

        synthesis = _cheap_synthesis()
        adapter = BrowserPlannerAdapter(backend=spy_backend, policy=PlannerTierPolicy())
        adapter.plan(synthesis)
        assert "strong" not in selected_tiers, (
            "strong tier must not be invoked for cheap synthesis"
        )

    def test_strong_tier_not_invoked_for_deterministic_synthesis(self) -> None:
        model_calls: list[str] = []

        def spy_backend(payload: Any) -> Any:
            model_calls.append(getattr(payload, "tier", "?"))
            return {"decision": "next_action", "rationale": "ok"}

        synthesis = _deterministic_synthesis()
        adapter = BrowserPlannerAdapter(backend=spy_backend, policy=PlannerTierPolicy())
        adapter.plan(synthesis)
        assert "strong" not in model_calls


# ---------------------------------------------------------------------------
# CONTRACT 4 – Planner input built from BrowserSynthesisResult, not raw DOM.
# ---------------------------------------------------------------------------


class TestPlannerInputIsCompactSemantic:
    def test_payload_built_from_synthesis_result(self) -> None:
        synthesis = _cheap_synthesis()
        payload = build_planner_payload(synthesis, policy=PlannerTierPolicy())
        assert isinstance(payload, PlannerPayload)

    def test_payload_excludes_raw_dom_fields(self) -> None:
        synthesis = _cheap_synthesis()
        payload = build_planner_payload(synthesis, policy=PlannerTierPolicy())
        serialized = json.dumps(payload.to_mapping())
        for forbidden in ("html", "screenshot_base64", "raw_dom", "full_page"):
            assert forbidden not in serialized, (
                f"planner payload must not contain raw browser field '{forbidden}'"
            )

    def test_payload_contains_semantic_evidence(self) -> None:
        synthesis = _strong_synthesis()
        payload = build_planner_payload(synthesis, policy=PlannerTierPolicy())
        data = payload.to_mapping()
        assert "evidence" in data or "conflicts" in data or "snapshots" in data, (
            "planner payload must contain semantic evidence/snapshot/conflict fields"
        )


# ---------------------------------------------------------------------------
# CONTRACT 5 – Hard size bounds; no secrets or provider credentials in payload.
# ---------------------------------------------------------------------------


class TestPayloadBoundsAndSecrets:
    def test_payload_respects_hard_byte_limit(self) -> None:
        synthesis = _strong_synthesis()
        policy = PlannerTierPolicy(max_payload_bytes=16_000)
        payload = build_planner_payload(synthesis, policy=policy)
        assert payload.serialized_size_bytes <= 16_000, (
            f"payload {payload.serialized_size_bytes} bytes exceeds 16 000-byte hard limit"
        )

    def test_payload_excludes_secret_credential_patterns(self) -> None:
        synthesis = _strong_synthesis()
        payload = build_planner_payload(synthesis, policy=PlannerTierPolicy())
        serialized = json.dumps(payload.to_mapping()).lower()
        for pattern in ("api_key", "secret", "password", "bearer ", "authorization:", "token="):
            assert pattern not in serialized, (
                f"planner payload must not contain credential pattern '{pattern}'"
            )


# ---------------------------------------------------------------------------
# CONTRACT 6 – Typed decision schema; no arbitrary executable code output.
# ---------------------------------------------------------------------------


class TestTypedDecisionOutput:
    def test_decision_kind_is_enum_member(self) -> None:
        synthesis = _deterministic_synthesis()
        adapter = BrowserPlannerAdapter(backend=None, policy=PlannerTierPolicy())
        decision = adapter.plan(synthesis)
        assert isinstance(decision.kind, PlannerDecisionKind)
        assert decision.kind in {
            PlannerDecisionKind.NEXT_ACTION,
            PlannerDecisionKind.GATHER_MORE_EVIDENCE,
            PlannerDecisionKind.STOP,
        }

    def test_decision_serializes_to_typed_mapping(self) -> None:
        synthesis = _deterministic_synthesis()
        adapter = BrowserPlannerAdapter(backend=None, policy=PlannerTierPolicy())
        decision = adapter.plan(synthesis)
        data = decision.to_mapping()
        assert "kind" in data
        assert data["kind"] in {"next_action", "gather_more_evidence", "stop"}
        assert "executable_code" not in data, (
            "decision must not carry arbitrary executable code"
        )

    def test_next_action_decision_has_action_description(self) -> None:
        def backend_next_action(payload: PlannerPayload) -> Any:
            return {"decision": "next_action", "action": "navigate", "target": "https://example.com/cart", "rationale": "proceed to cart"}

        synthesis = _cheap_synthesis()
        adapter = BrowserPlannerAdapter(backend=backend_next_action, policy=PlannerTierPolicy())
        decision = adapter.plan(synthesis)
        if decision.kind == PlannerDecisionKind.NEXT_ACTION:
            assert decision.action is not None
            assert isinstance(decision.action, str)
            assert "eval(" not in decision.action
            assert "exec(" not in decision.action


# ---------------------------------------------------------------------------
# CONTRACT 7 – click/fill decisions subject to existing approval boundaries.
# ---------------------------------------------------------------------------


class TestWriteActionApprovalBoundary:
    def test_click_decision_is_not_directly_executed(self) -> None:
        """A planner decision to click must not immediately call the browser driver."""
        executed: list[str] = []

        def mock_executor(action: str, target: str, **kwargs: Any) -> None:
            executed.append(action)

        def backend_click(payload: PlannerPayload) -> Any:
            return {"decision": "next_action", "action": "click", "target": "#submit", "rationale": "submit form"}

        synthesis = _cheap_synthesis()
        adapter = BrowserPlannerAdapter(
            backend=backend_click,
            policy=PlannerTierPolicy(),
            executor=mock_executor,
        )
        decision = adapter.plan(synthesis)
        assert "click" not in executed, (
            "click action must not be executed directly by the planner; "
            "it must go through the approval boundary"
        )
        assert decision.kind == PlannerDecisionKind.NEXT_ACTION
        assert decision.requires_approval is True

    def test_fill_decision_requires_approval_flag(self) -> None:
        def backend_fill(payload: PlannerPayload) -> Any:
            return {"decision": "next_action", "action": "fill", "target": "#email", "value": "user@example.com", "rationale": "enter email"}

        synthesis = _cheap_synthesis()
        adapter = BrowserPlannerAdapter(backend=backend_fill, policy=PlannerTierPolicy())
        decision = adapter.plan(synthesis)
        if decision.kind == PlannerDecisionKind.NEXT_ACTION and decision.action in ("fill", "click"):
            assert decision.requires_approval is True, (
                "write actions (fill/click) must carry requires_approval=True"
            )


# ---------------------------------------------------------------------------
# CONTRACT 8 – Backend/model identity does not expand planner authority.
# ---------------------------------------------------------------------------


class TestBackendDoesNotExpandAuthority:
    def test_strong_backend_cannot_bypass_dispatcher(self) -> None:
        """Even a 'strong' model backend may not call RuntimeDispatcher methods directly."""
        dispatcher_calls: list[str] = []

        class FakeDispatcher:
            def execute(self, *args: Any, **kwargs: Any) -> Any:
                dispatcher_calls.append("execute")
                return {}

        def backend_bypass_attempt(payload: PlannerPayload) -> Any:
            return {"decision": "next_action", "action": "navigate", "target": "https://example.com", "rationale": "test"}

        synthesis = _strong_synthesis()
        fake_dispatcher = FakeDispatcher()
        adapter = BrowserPlannerAdapter(
            backend=backend_bypass_attempt,
            policy=PlannerTierPolicy(),
            dispatcher=fake_dispatcher,
        )
        adapter.plan(synthesis)
        assert not dispatcher_calls, (
            "planner adapter must not call RuntimeDispatcher.execute() directly; "
            "runtime access must go through UniversalToolExecutor"
        )

    def test_model_backend_choice_does_not_change_decision_schema(self) -> None:
        """Switching backend/model must not change the PlannerDecision schema."""
        def cheap_backend(payload: PlannerPayload) -> Any:
            return {"decision": "stop", "rationale": "done"}

        def strong_backend(payload: PlannerPayload) -> Any:
            return {"decision": "stop", "rationale": "done"}

        cheap_synthesis = _cheap_synthesis()
        strong_synthesis = _strong_synthesis()

        adapter_cheap = BrowserPlannerAdapter(backend=cheap_backend, policy=PlannerTierPolicy())
        adapter_strong = BrowserPlannerAdapter(backend=strong_backend, policy=PlannerTierPolicy())

        decision_cheap = adapter_cheap.plan(cheap_synthesis)
        decision_strong = adapter_strong.plan(strong_synthesis)

        assert set(decision_cheap.to_mapping()) == set(decision_strong.to_mapping()), (
            "PlannerDecision schema must be identical regardless of model backend"
        )


# ---------------------------------------------------------------------------
# CONTRACT 9 – Execution Trace stores bounded tier/token/provenance without
#              duplicating the full planner prompt or secrets.
# ---------------------------------------------------------------------------


class TestExecutionTraceMetadata:
    def test_trace_records_tier_and_tokens(self) -> None:
        def backend(payload: PlannerPayload) -> Any:
            return {"decision": "stop", "rationale": "done", "tokens_used": 42}

        synthesis = _cheap_synthesis()
        adapter = BrowserPlannerAdapter(backend=backend, policy=PlannerTierPolicy())
        decision = adapter.plan(synthesis)
        trace = decision.trace_metadata
        assert isinstance(trace, Mapping)
        assert "tier" in trace
        assert "tokens" in trace or "consumed_tokens" in trace

    def test_trace_does_not_duplicate_full_prompt(self) -> None:
        captured_prompts: list[str] = []

        def backend(payload: PlannerPayload) -> Any:
            captured_prompts.append(str(payload.to_mapping()))
            return {"decision": "stop", "rationale": "done"}

        synthesis = _cheap_synthesis()
        adapter = BrowserPlannerAdapter(backend=backend, policy=PlannerTierPolicy())
        decision = adapter.plan(synthesis)
        trace = decision.trace_metadata
        trace_str = json.dumps(trace)
        if captured_prompts:
            assert trace_str != captured_prompts[0], (
                "trace metadata must not duplicate the full planner prompt"
            )
        assert len(trace_str) <= 4_096, (
            f"trace metadata ({len(trace_str)} bytes) must be bounded to 4 096 bytes"
        )

    def test_trace_excludes_secrets(self) -> None:
        def backend(payload: PlannerPayload) -> Any:
            return {"decision": "stop", "rationale": "done"}

        synthesis = _strong_synthesis()
        adapter = BrowserPlannerAdapter(backend=backend, policy=PlannerTierPolicy())
        decision = adapter.plan(synthesis)
        trace_str = json.dumps(decision.trace_metadata).lower()
        for pattern in ("api_key", "secret", "password", "bearer ", "token="):
            assert pattern not in trace_str, (
                f"trace metadata must not contain secret pattern '{pattern}'"
            )


# ---------------------------------------------------------------------------
# CONTRACT 10 – Unchanged synthesis evidence must not repeatedly trigger
#               strong reasoning without new evidence.
# ---------------------------------------------------------------------------


class TestCacheReuseOnUnchangedEvidence:
    def test_repeated_strong_calls_with_identical_evidence_are_deduped(self) -> None:
        strong_calls: list[int] = []

        def strong_backend(payload: PlannerPayload) -> Any:
            strong_calls.append(1)
            return {"decision": "stop", "rationale": "resolved"}

        synthesis = _strong_synthesis()
        policy = PlannerTierPolicy(reuse_strong_on_unchanged_evidence=True)
        adapter = BrowserPlannerAdapter(backend=strong_backend, policy=policy)

        adapter.plan(synthesis)
        adapter.plan(synthesis)

        assert len(strong_calls) == 1, (
            "identical synthesis evidence must not trigger a second strong backend call; "
            f"got {len(strong_calls)} call(s)"
        )

    def test_changed_evidence_triggers_new_strong_call(self) -> None:
        strong_calls: list[int] = []

        def strong_backend(payload: PlannerPayload) -> Any:
            strong_calls.append(1)
            return {"decision": "stop", "rationale": "resolved"}

        synthesis_a = _strong_synthesis()
        evidence_b = MergedEvidence(
            facts={},
            conflicts={"price": ({"value": 99, "confidence": 0.9, "sources": ["new"]},)},
            sources={},
        )
        escalation_b = EscalationDecision(
            required=True, suggested_tier="strong", reasons=("evidence_conflicts:price",)
        )
        synthesis_b = BrowserSynthesisResult(
            status="succeeded",
            task_statuses={"t1": "succeeded"},
            snapshots={"t1": _make_snapshot()},
            diffs={},
            evidence=evidence_b,
            consumed_tokens=20,
            escalation=escalation_b,
        )

        policy = PlannerTierPolicy(reuse_strong_on_unchanged_evidence=True)
        adapter = BrowserPlannerAdapter(backend=strong_backend, policy=policy)

        adapter.plan(synthesis_a)
        adapter.plan(synthesis_b)

        assert len(strong_calls) == 2, (
            "changed evidence must trigger a new strong backend call; "
            f"got {len(strong_calls)} call(s)"
        )


# ---------------------------------------------------------------------------
# CONTRACT 11 – Compact semantic payload is measurably smaller than raw browser.
# ---------------------------------------------------------------------------


class TestCompactPayloadSmallerThanRaw:
    def test_semantic_payload_smaller_than_raw_browser_payload(self) -> None:
        synthesis = _strong_synthesis()
        payload = build_planner_payload(synthesis, policy=PlannerTierPolicy())
        semantic_bytes = payload.serialized_size_bytes
        raw_bytes = _raw_browser_payload_bytes()
        ratio = semantic_bytes / raw_bytes if raw_bytes > 0 else float("inf")
        assert semantic_bytes < raw_bytes, (
            f"semantic planner payload ({semantic_bytes} bytes) must be smaller than "
            f"equivalent raw browser payload ({raw_bytes} bytes); ratio={ratio:.3f}"
        )
        assert ratio < 0.5, (
            f"semantic payload must be < 50 % of raw payload; ratio={ratio:.3f}"
        )


# ---------------------------------------------------------------------------
# CONTRACT 12 – Owner/runtime isolation stays intact.
# ---------------------------------------------------------------------------


class TestOwnerRuntimeIsolation:
    def test_planner_does_not_cross_runtime_boundary(self) -> None:
        """BrowserPlannerAdapter must not access resources outside its runtime scope."""
        cross_runtime_calls: list[str] = []

        class MockRuntimeDispatcher:
            def get_resource(self, runtime_id: str, *args: Any, **kwargs: Any) -> Any:
                cross_runtime_calls.append(runtime_id)
                return {}

        def backend(payload: PlannerPayload) -> Any:
            return {"decision": "stop", "rationale": "done"}

        synthesis = _deterministic_synthesis()
        dispatcher = MockRuntimeDispatcher()
        adapter = BrowserPlannerAdapter(
            backend=backend,
            policy=PlannerTierPolicy(),
            dispatcher=dispatcher,
            runtime_id="browser-runtime-1",
        )
        adapter.plan(synthesis)
        foreign_calls = [r for r in cross_runtime_calls if r != "browser-runtime-1"]
        assert not foreign_calls, (
            f"planner adapter accessed foreign runtime(s): {foreign_calls}"
        )

    def test_planner_adapter_cannot_instantiate_without_runtime_context(self) -> None:
        """BrowserPlannerAdapter must validate that runtime context is provided."""
        with pytest.raises((TypeError, ValueError)):
            BrowserPlannerAdapter(backend=None, policy=None, runtime_id=None)
