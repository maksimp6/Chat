import json

from agent_context import (
    ContextSlice,
    EvidenceRef,
    EvidenceVersion,
    TaskContextCache,
    TaskPacket,
    TaskScope,
    record_context_cache_lookup,
)
from trace_manager import ExecutionTrace


def _scope(*, head="head-1", skills=("github-ci-diagnosis", "github-pr-readiness")):
    return TaskScope(
        repository="maksimp6/Chat",
        work_item="PR#548",
        base_sha="base-1",
        head_sha=head,
        role="Test Engineer",
        selected_skills=skills,
    )


def _evidence(
    *,
    ci="ci-1",
    review="review-1",
    trace="trace-1",
    files="files-1",
    skills="skills-1",
):
    return EvidenceVersion(
        ci=ci,
        review=review,
        trace=trace,
        files=files,
        skills=skills,
        policy="policy-1",
    )


def _packet(scope=None, evidence=None):
    return TaskPacket(
        scope=scope or _scope(),
        evidence=evidence or _evidence(),
        objective="Diagnose rollback timestamp regression",
        expected_deliverable="Evidence-backed next action",
        known_facts=("PR is behind master",),
        open_questions=("Does current master still reproduce the failure?",),
        failed_attempts=("Do not trust stale green CI",),
        evidence_refs=(
            EvidenceRef(
                kind="github_pr",
                ref="https://github.com/maksimp6/Chat/pull/548",
            ),
        ),
        slices=(
            ContextSlice(
                name="code",
                payload={"summary": "timezone parser and regression"},
                depends_on=("files", "policy"),
                source_bytes=1200,
                input_tokens=220,
            ),
            ContextSlice(
                name="ci",
                payload={"conclusion": "success"},
                depends_on=("ci",),
                source_bytes=400,
                input_tokens=70,
            ),
            ContextSlice(
                name="review",
                payload={"threads": 0},
                depends_on=("review",),
                source_bytes=200,
                input_tokens=35,
            ),
            ContextSlice(
                name="skills",
                payload={"versions": ["github-ci-diagnosis@1"]},
                depends_on=("skills",),
                source_bytes=100,
                input_tokens=20,
            ),
        ),
        changed_files=(
            "provider_credentials.py",
            "tests/test_provider_credentials_secret_management.py",
        ),
        owner="Test Engineer",
        budget_tier="cheap",
        usage={"cheap_calls": 2, "normal_calls": 0, "strong_calls": 0},
    )


def test_scope_rejects_missing_authoritative_provenance():
    for field in ("repository", "work_item", "base_sha", "head_sha", "role"):
        kwargs = {
            "repository": "maksimp6/Chat",
            "work_item": "PR#548",
            "base_sha": "base-1",
            "head_sha": "head-1",
            "role": "Test Engineer",
        }
        kwargs[field] = None
        try:
            TaskScope(**kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError(f"{field} must be required")


def test_scope_and_cache_key_are_deterministic():
    first = _scope(skills=("github-pr-readiness", "github-ci-diagnosis"))
    second = _scope(skills=("github-ci-diagnosis", "github-pr-readiness"))

    assert first.selected_skills == second.selected_skills
    assert first.scope_key() == second.scope_key()
    assert _packet(scope=first).cache_key() == _packet(scope=second).cache_key()


def test_task_packet_contains_canonical_handoff_fields():
    packet = _packet()
    payload = packet.as_dict()

    assert payload["scope"]["work_item"] == "PR#548"
    assert payload["changed_files"] == [
        "provider_credentials.py",
        "tests/test_provider_credentials_secret_management.py",
    ]
    assert payload["owner"] == "Test Engineer"
    assert payload["budget_tier"] == "cheap"
    assert payload["usage"]["cheap_calls"] == 2


def test_exact_head_and_evidence_reuse_is_a_hit():
    cache = TaskContextCache()
    packet = _packet()
    cache.put(packet)

    result = cache.lookup(packet.scope, packet.evidence)

    assert result.status == "hit"
    assert result.packet == packet
    assert [item.name for item in result.reusable_slices] == [
        "code",
        "ci",
        "review",
        "skills",
    ]
    assert result.saved_source_bytes == 1900
    assert result.saved_input_tokens == 345


def test_new_head_is_full_miss_even_when_other_evidence_matches():
    cache = TaskContextCache()
    cache.put(_packet())

    result = cache.lookup(_scope(head="head-2"), _evidence())

    assert result.status == "miss"
    assert result.packet is None
    assert result.reusable_slices == ()


def test_changed_ci_reuses_only_slices_independent_of_ci():
    cache = TaskContextCache()
    packet = _packet()
    cache.put(packet)

    result = cache.lookup(packet.scope, _evidence(ci="ci-2"))

    assert result.status == "partial"
    assert result.packet is None
    assert result.stale_components == ("ci",)
    assert [item.name for item in result.reusable_slices] == [
        "code",
        "review",
        "skills",
    ]
    assert result.saved_source_bytes == 1500
    assert result.saved_input_tokens == 275


def test_multiple_evidence_changes_keep_only_independent_slices():
    cache = TaskContextCache()
    packet = _packet()
    cache.put(packet)

    result = cache.lookup(packet.scope, _evidence(ci="ci-2", review="review-2"))

    assert result.status == "partial"
    assert result.stale_components == ("ci", "review")
    assert [item.name for item in result.reusable_slices] == ["code", "skills"]


def test_changed_skill_version_invalidates_only_skill_dependent_slice():
    cache = TaskContextCache()
    packet = _packet()
    cache.put(packet)

    result = cache.lookup(packet.scope, _evidence(skills="skills-2"))

    assert result.status == "partial"
    assert result.stale_components == ("skills",)
    assert [item.name for item in result.reusable_slices] == ["code", "ci", "review"]


def test_all_slice_dependencies_stale_becomes_miss():
    cache = TaskContextCache()
    packet = TaskPacket(
        scope=_scope(),
        evidence=_evidence(),
        objective="Check readiness",
        expected_deliverable="Readiness decision",
        slices=(
            ContextSlice(name="ci", payload={"ok": True}, depends_on=("ci",)),
            ContextSlice(
                name="review",
                payload={"open": 0},
                depends_on=("review",),
            ),
        ),
    )
    cache.put(packet)

    result = cache.lookup(packet.scope, _evidence(ci="ci-2", review="review-2"))

    assert result.status == "miss"
    assert result.packet is None
    assert result.stale_components == ("ci", "review")
    assert result.reusable_slices == ()


def test_sensitive_payloads_are_redacted_before_cache_storage():
    packet = TaskPacket(
        scope=_scope(),
        evidence=_evidence(),
        objective="Inspect auth without leaking token=supersecret",
        expected_deliverable="Safe result",
        usage={"access_token": "supersecret"},
        slices=(
            ContextSlice(
                name="auth",
                payload={
                    "access_token": "supersecret",
                    "note": "authorization: Bearer supersecret",
                },
            ),
        ),
    )

    serialized = packet.as_dict()

    assert "supersecret" not in json.dumps(serialized)
    assert serialized["usage"]["access_token"] == "<redacted>"
    assert serialized["slices"][0]["payload"]["access_token"] == "<redacted>"


def test_cache_lookup_is_recorded_on_execution_trace():
    cache = TaskContextCache()
    packet = _packet()
    cache.put(packet)
    result = cache.lookup(packet.scope, packet.evidence)
    trace = ExecutionTrace("trace-cache")

    entry = record_context_cache_lookup(trace, result, packet.scope)

    assert entry["status"] == "hit"
    assert entry["head_sha"] == "head-1"
    assert entry["saved_input_tokens"] == 345
    assert trace.trace["context_cache_operations"][0]["work_item"] == "PR#548"
    assert trace.trace["events"][-1]["type"] == "context_cache_lookup"
    json.dumps(trace.finalize())


def test_scope_invalidation_removes_all_evidence_versions():
    cache = TaskContextCache()
    scope = _scope()
    cache.put(_packet(scope=scope, evidence=_evidence(ci="ci-1")))
    cache.put(_packet(scope=scope, evidence=_evidence(ci="ci-2")))

    removed = cache.invalidate_scope(scope)

    assert removed == 2
    assert len(cache) == 0
    assert cache.lookup(scope, _evidence(ci="ci-2")).status == "miss"

def test_cache_lookup_rejects_invalid_status():
    try:
        from agent_context.cache import CacheLookup

        CacheLookup(status="stale", cache_key="key")
    except ValueError:
        pass
    else:
        raise AssertionError("invalid cache status must be rejected")


def test_cache_missing_entry_behind_latest_pointer_fails_closed():
    cache = TaskContextCache()
    packet = _packet()
    cache_key = cache.put(packet)
    cache._entries.pop(cache_key)

    result = cache.lookup(packet.scope, _evidence(ci="ci-2"))

    assert result.status == "miss"
    assert result.packet is None


def test_evidence_fingerprint_is_deterministic():
    first = _evidence()
    second = _evidence()

    assert first.fingerprint() == second.fingerprint()


def test_evidence_ref_requires_kind_and_reference():
    for kwargs in (
        {"kind": "", "ref": "ref"},
        {"kind": "github_pr", "ref": ""},
    ):
        try:
            EvidenceRef(**kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError("evidence kind and ref must be required")


def test_context_slice_validates_name_dependencies_and_usage():
    invalid_cases = (
        {"name": "", "payload": {}},
        {"name": "bad-dependency", "payload": {}, "depends_on": ("unknown",)},
        {"name": "bad-bytes", "payload": {}, "source_bytes": -1},
        {"name": "bad-tokens", "payload": {}, "input_tokens": -1},
    )
    for kwargs in invalid_cases:
        try:
            ContextSlice(**kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid context slice accepted: {kwargs['name']}")


def test_task_packet_requires_objective_and_deliverable():
    for objective, deliverable in (("", "result"), ("task", "")):
        try:
            TaskPacket(
                scope=_scope(),
                evidence=_evidence(),
                objective=objective,
                expected_deliverable=deliverable,
            )
        except ValueError:
            pass
        else:
            raise AssertionError("blank task packet contract field must be rejected")


def test_task_packet_sanitizes_escalation_target():
    packet = TaskPacket(
        scope=_scope(),
        evidence=_evidence(),
        objective="Escalate safely",
        expected_deliverable="Safe escalation",
        escalation_target="authorization: Bearer supersecret",
    )

    assert "supersecret" not in packet.escalation_target
    assert "<redacted>" in packet.escalation_target

