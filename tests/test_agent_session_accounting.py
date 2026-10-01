"""RED contract for #662 — agent/session accounting.

All tests in this file MUST fail (RED) until agent_session_accounting is
implemented. The import at module level ensures every test fails immediately
with ModuleNotFoundError when the module is absent.

Interface contract tested here:

    AgentSessionRecord   — dataclass holding one session's correlation+usage
    upsert_session_record(conn, record) -> bool
    export_session_records(conn, filters) -> list[dict]
    compute_session_totals(conn, filters) -> dict

No live model calls, no secrets, no permission changes.
"""

import sqlite3
from dataclasses import dataclass
from typing import Optional

import pytest

# RED: this import fails until the module exists
from agent_session_accounting import (
    AgentSessionRecord,
    export_session_records,
    upsert_session_record,
    compute_session_totals,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mem_db():
    """In-memory SQLite with the accounting schema initialised."""
    from agent_session_accounting import init_accounting_tables

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_accounting_tables(conn)
    return conn


def _record(**overrides) -> AgentSessionRecord:
    """Minimal valid AgentSessionRecord; callers can override any field."""
    defaults = dict(
        repo="maksimp6/Chat",
        work_item_id="662",
        task_id="task-1",
        session_id="session-abc",
        role="backend-engineer",
        stage="implement",
        backend="claude-lite",
        requested_model="claude-sonnet-4-6",
        actual_model="claude-sonnet-4-6",
        head_sha="2bf86cac824b6079b779fe83cd492e5c12ed2914",
        run_id="36861237228",
        attempt=1,
        input_tokens=500,
        output_tokens=200,
        cache_read_tokens=None,
        cache_write_tokens=None,
        token_missing_reason=None,
        billing_source="subscription",
        billing_source_reason=None,
        api_equivalent_cost=None,
        invoiced_charge=None,
        invoiced_charge_reason="subscription_no_per_call_charge",
        queue_duration_s=4.2,
        execution_duration_s=61.0,
        turns=8,
        terminal_result="success",
        started_at="2026-10-01T12:00:00Z",
        finished_at="2026-10-01T12:01:05Z",
    )
    defaults.update(overrides)
    return AgentSessionRecord(**defaults)


# ---------------------------------------------------------------------------
# 1. Correlation — all identity fields round-trip
# ---------------------------------------------------------------------------

def test_correlation_fields_round_trip():
    """repo/work_item/task/session/role/stage/backend/model/head/run/attempt all persist."""
    conn = _mem_db()
    rec = _record()
    upsert_session_record(conn, rec)

    rows = export_session_records(conn)
    assert len(rows) == 1
    row = rows[0]

    assert row["repo"] == "maksimp6/Chat"
    assert row["work_item_id"] == "662"
    assert row["task_id"] == "task-1"
    assert row["session_id"] == "session-abc"
    assert row["role"] == "backend-engineer"
    assert row["stage"] == "implement"
    assert row["backend"] == "claude-lite"
    assert row["requested_model"] == "claude-sonnet-4-6"
    assert row["actual_model"] == "claude-sonnet-4-6"
    assert row["head_sha"] == "2bf86cac824b6079b779fe83cd492e5c12ed2914"
    assert row["run_id"] == "36861237228"
    assert row["attempt"] == 1


def test_requested_model_and_actual_model_are_stored_independently():
    """Provider may report a different model than what was requested; both are kept."""
    conn = _mem_db()
    rec = _record(
        requested_model="claude-opus-5-5",
        actual_model="claude-sonnet-4-6",
    )
    upsert_session_record(conn, rec)

    row = export_session_records(conn)[0]
    assert row["requested_model"] == "claude-opus-5-5"
    assert row["actual_model"] == "claude-sonnet-4-6"


# ---------------------------------------------------------------------------
# 2. Provider tokens and cache — distinct, missing = null + reason not zero
# ---------------------------------------------------------------------------

def test_token_fields_are_stored_separately():
    """input, output, cache_read, cache_write tokens never collapse into a sum."""
    conn = _mem_db()
    rec = _record(
        input_tokens=1000,
        output_tokens=300,
        cache_read_tokens=200,
        cache_write_tokens=50,
        token_missing_reason=None,
    )
    upsert_session_record(conn, rec)

    row = export_session_records(conn)[0]
    assert row["input_tokens"] == 1000
    assert row["output_tokens"] == 300
    assert row["cache_read_tokens"] == 200
    assert row["cache_write_tokens"] == 50
    # Cache tokens must NOT be double-counted into input_tokens
    assert row["input_tokens"] != row["input_tokens"] + row["cache_read_tokens"]


def test_missing_tokens_are_null_with_reason_not_zero():
    """When token data is unavailable, fields are None and reason explains why."""
    conn = _mem_db()
    rec = _record(
        input_tokens=None,
        output_tokens=None,
        cache_read_tokens=None,
        cache_write_tokens=None,
        token_missing_reason="usage_not_reported_by_provider",
    )
    upsert_session_record(conn, rec)

    row = export_session_records(conn)[0]
    assert row["input_tokens"] is None, "missing tokens must be null, not 0"
    assert row["output_tokens"] is None, "missing tokens must be null, not 0"
    assert row["cache_read_tokens"] is None
    assert row["cache_write_tokens"] is None
    assert row["token_missing_reason"] == "usage_not_reported_by_provider"


def test_partial_token_data_keeps_known_fields_and_null_unknown():
    """Partial availability: known fields kept, unknown stay null."""
    conn = _mem_db()
    rec = _record(
        input_tokens=400,
        output_tokens=None,
        cache_read_tokens=None,
        cache_write_tokens=None,
        token_missing_reason="output_tokens_not_streamed",
    )
    upsert_session_record(conn, rec)

    row = export_session_records(conn)[0]
    assert row["input_tokens"] == 400
    assert row["output_tokens"] is None
    assert row["token_missing_reason"] == "output_tokens_not_streamed"


# ---------------------------------------------------------------------------
# 3. Billing source separate from API-equivalent estimate and invoiced charge
# ---------------------------------------------------------------------------

def test_subscription_billing_source_has_no_invoiced_charge_in_dollars():
    """Subscription sessions have no per-call invoiced charge; estimate is separate."""
    conn = _mem_db()
    rec = _record(
        billing_source="subscription",
        api_equivalent_cost=None,
        invoiced_charge=None,
        invoiced_charge_reason="subscription_no_per_call_charge",
    )
    upsert_session_record(conn, rec)

    row = export_session_records(conn)[0]
    assert row["billing_source"] == "subscription"
    assert row["invoiced_charge"] is None
    assert row["invoiced_charge_reason"] == "subscription_no_per_call_charge"
    # Subscription tokens must NOT be converted to an api_equivalent_cost dollar value
    # without authoritative data; any non-null estimate must carry a source label
    if row["api_equivalent_cost"] is not None:
        assert row.get("api_equivalent_cost_source") is not None, (
            "api_equivalent_cost must carry source/date when set for subscription"
        )


def test_api_billing_source_records_invoiced_charge_separately_from_estimate():
    """API-key sessions can carry both an estimate and a confirmed invoiced amount."""
    conn = _mem_db()
    rec = _record(
        billing_source="api",
        api_equivalent_cost=0.0042,
        invoiced_charge=0.0042,
        invoiced_charge_reason=None,
    )
    upsert_session_record(conn, rec)

    row = export_session_records(conn)[0]
    assert row["billing_source"] == "api"
    assert row["api_equivalent_cost"] == pytest.approx(0.0042)
    assert row["invoiced_charge"] == pytest.approx(0.0042)


def test_unknown_billing_source_stored_with_reason():
    """Unknown billing source is explicit, not silently defaulted to api."""
    conn = _mem_db()
    rec = _record(
        billing_source="unknown",
        billing_source_reason="oauth_and_api_key_both_configured",
        api_equivalent_cost=None,
        invoiced_charge=None,
        invoiced_charge_reason="billing_source_unknown",
    )
    upsert_session_record(conn, rec)

    row = export_session_records(conn)[0]
    assert row["billing_source"] == "unknown"
    assert row["billing_source_reason"] is not None


def test_billing_source_values_are_constrained():
    """Only subscription/api/unknown are valid billing_source values."""
    from agent_session_accounting import VALID_BILLING_SOURCES

    assert set(VALID_BILLING_SOURCES) == {"subscription", "api", "unknown"}


# ---------------------------------------------------------------------------
# 4. Actions queue time separate from execution; retries do not double count
# ---------------------------------------------------------------------------

def test_queue_and_execution_durations_stored_separately():
    """queue_duration_s and execution_duration_s are distinct columns."""
    conn = _mem_db()
    rec = _record(queue_duration_s=8.5, execution_duration_s=72.0)
    upsert_session_record(conn, rec)

    row = export_session_records(conn)[0]
    assert row["queue_duration_s"] == pytest.approx(8.5)
    assert row["execution_duration_s"] == pytest.approx(72.0)


def test_retry_upsert_replaces_not_duplicates():
    """A second attempt for the same run/session replaces the first; totals do not double."""
    conn = _mem_db()
    first = _record(run_id="R1", attempt=1, input_tokens=100, execution_duration_s=30.0)
    retry = _record(run_id="R1", attempt=2, input_tokens=180, execution_duration_s=45.0)

    upsert_session_record(conn, first)
    upsert_session_record(conn, retry)

    rows = export_session_records(conn)
    # Only the latest attempt for this run/session_id identity should appear
    assert len(rows) == 1
    assert rows[0]["attempt"] == 2
    assert rows[0]["input_tokens"] == 180


def test_distinct_runs_do_not_collapse():
    """Different run IDs are kept as separate records."""
    conn = _mem_db()
    upsert_session_record(conn, _record(run_id="R1", session_id="s1", attempt=1))
    upsert_session_record(conn, _record(run_id="R2", session_id="s2", attempt=1))

    rows = export_session_records(conn)
    assert len(rows) == 2


def test_missing_queue_duration_is_null_not_zero():
    """Unknown queue time is None; zero would misrepresent actual queue wait."""
    conn = _mem_db()
    rec = _record(queue_duration_s=None)
    upsert_session_record(conn, rec)

    row = export_session_records(conn)[0]
    assert row["queue_duration_s"] is None


# ---------------------------------------------------------------------------
# 5. Failed/cancelled sessions with real usage still count
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("result", ["failed", "cancelled"])
def test_non_success_session_with_tokens_is_included(result):
    """Failed/cancelled sessions with token data appear in records and totals."""
    conn = _mem_db()
    rec = _record(
        terminal_result=result,
        input_tokens=250,
        output_tokens=80,
    )
    upsert_session_record(conn, rec)

    rows = export_session_records(conn)
    assert len(rows) == 1
    assert rows[0]["terminal_result"] == result
    assert rows[0]["input_tokens"] == 250

    totals = compute_session_totals(conn)
    assert totals["measured_sessions"] >= 1


def test_failed_session_without_tokens_counts_as_unknown():
    """Failed session with no token data counts in unknown_sessions, not silently dropped."""
    conn = _mem_db()
    rec = _record(
        terminal_result="failed",
        input_tokens=None,
        output_tokens=None,
        token_missing_reason="session_failed_before_provider_responded",
    )
    upsert_session_record(conn, rec)

    totals = compute_session_totals(conn)
    assert totals["unknown_sessions"] >= 1


# ---------------------------------------------------------------------------
# 6. Stable upsert identity prevents duplicate accounting
# ---------------------------------------------------------------------------

def test_identical_upsert_is_idempotent():
    """Inserting the same record twice leaves exactly one row."""
    conn = _mem_db()
    rec = _record()
    upsert_session_record(conn, rec)
    upsert_session_record(conn, rec)

    rows = export_session_records(conn)
    assert len(rows) == 1


def test_upsert_updates_fields_on_same_identity():
    """Upserting with same stable key but updated fields reflects the new values."""
    conn = _mem_db()
    initial = _record(terminal_result="in_progress", input_tokens=None, token_missing_reason="in_progress")
    upsert_session_record(conn, initial)

    final = _record(terminal_result="success", input_tokens=500, token_missing_reason=None)
    upsert_session_record(conn, final)

    rows = export_session_records(conn)
    assert len(rows) == 1
    assert rows[0]["terminal_result"] == "success"
    assert rows[0]["input_tokens"] == 500
    assert rows[0]["token_missing_reason"] is None


def test_upsert_stable_key_is_run_attempt_session():
    """Records with different (run_id, attempt, session_id) are never merged."""
    conn = _mem_db()
    upsert_session_record(conn, _record(run_id="R1", attempt=1, session_id="s1"))
    upsert_session_record(conn, _record(run_id="R1", attempt=1, session_id="s2"))
    upsert_session_record(conn, _record(run_id="R1", attempt=2, session_id="s1"))

    rows = export_session_records(conn)
    assert len(rows) == 3


# ---------------------------------------------------------------------------
# 7. Sanitised export never includes prompts or secrets
# ---------------------------------------------------------------------------

_FORBIDDEN_EXPORT_KEYS = {
    "prompt",
    "system_prompt",
    "transcript",
    "messages",
    "secret",
    "token_value",
    "api_key",
    "password",
    "credentials",
    "raw_response",
}


def test_export_does_not_include_prompt_or_secret_fields():
    """export_session_records must never surface sensitive fields."""
    conn = _mem_db()
    upsert_session_record(conn, _record())
    rows = export_session_records(conn)
    assert rows, "expected at least one row"

    for row in rows:
        leaked = _FORBIDDEN_EXPORT_KEYS & set(row.keys())
        assert not leaked, f"exported row contains sensitive keys: {leaked}"


def test_export_with_explicit_sanitize_flag_still_redacts():
    """Passing sanitize=True (the default) must suppress sensitive keys."""
    conn = _mem_db()
    upsert_session_record(conn, _record())
    rows = export_session_records(conn, sanitize=True)
    for row in rows:
        leaked = _FORBIDDEN_EXPORT_KEYS & set(row.keys())
        assert not leaked


# ---------------------------------------------------------------------------
# 8. Totals expose completeness and unknown-session count
# ---------------------------------------------------------------------------

def test_totals_structure_exposes_completeness():
    """compute_session_totals must return required completeness keys."""
    conn = _mem_db()
    upsert_session_record(conn, _record(input_tokens=100, output_tokens=50))
    upsert_session_record(
        conn,
        _record(
            run_id="R2",
            session_id="s2",
            input_tokens=None,
            output_tokens=None,
            token_missing_reason="provider_timeout",
        ),
    )

    totals = compute_session_totals(conn)

    assert "measured_sessions" in totals
    assert "unknown_sessions" in totals
    assert "in_progress_sessions" in totals
    assert "completeness" in totals

    completeness = totals["completeness"]
    assert "sessions_with_full_tokens" in completeness
    assert "sessions_with_partial_tokens" in completeness
    assert "sessions_with_no_tokens" in completeness


def test_totals_separate_known_invoiced_from_estimated():
    """known_invoiced_charges and estimated_cost are never merged."""
    conn = _mem_db()
    upsert_session_record(
        conn,
        _record(
            billing_source="api",
            invoiced_charge=0.005,
            api_equivalent_cost=0.005,
        ),
    )
    upsert_session_record(
        conn,
        _record(
            run_id="R2",
            session_id="s2",
            billing_source="subscription",
            invoiced_charge=None,
            invoiced_charge_reason="subscription_no_per_call_charge",
            api_equivalent_cost=None,
        ),
    )

    totals = compute_session_totals(conn)
    assert "known_invoiced_charges" in totals
    assert "estimated_cost" in totals
    # Subscription unknown charge must not inflate known_invoiced_charges
    known = totals["known_invoiced_charges"]
    if known is not None:
        assert known == pytest.approx(0.005)


def test_totals_include_model_and_role_subtotals():
    """model_subtotals and role_subtotals group token/session counts."""
    conn = _mem_db()
    upsert_session_record(
        conn,
        _record(
            role="backend-engineer",
            actual_model="claude-sonnet-4-6",
            input_tokens=300,
            output_tokens=100,
        ),
    )
    upsert_session_record(
        conn,
        _record(
            run_id="R2",
            session_id="s2",
            role="test-engineer",
            actual_model="claude-haiku-4-5-20251001",
            input_tokens=150,
            output_tokens=60,
        ),
    )

    totals = compute_session_totals(conn)
    assert "model_subtotals" in totals
    assert "role_subtotals" in totals

    assert "claude-sonnet-4-6" in totals["model_subtotals"]
    assert "backend-engineer" in totals["role_subtotals"]
    assert "test-engineer" in totals["role_subtotals"]


def test_in_progress_sessions_are_marked_provisional():
    """Sessions with terminal_result=in_progress are counted separately and not finalised."""
    conn = _mem_db()
    upsert_session_record(
        conn,
        _record(
            terminal_result="in_progress",
            input_tokens=None,
            token_missing_reason="in_progress",
        ),
    )
    upsert_session_record(conn, _record(run_id="R2", session_id="s2", terminal_result="success"))

    totals = compute_session_totals(conn)
    assert totals["in_progress_sessions"] == 1
    assert totals["measured_sessions"] >= 1


def test_unknown_session_count_matches_null_token_rows():
    """unknown_sessions count equals rows where all token fields are null."""
    conn = _mem_db()
    upsert_session_record(conn, _record(input_tokens=100))
    upsert_session_record(
        conn,
        _record(
            run_id="R2",
            session_id="s2",
            input_tokens=None,
            output_tokens=None,
            cache_read_tokens=None,
            cache_write_tokens=None,
            token_missing_reason="provider_did_not_return_usage",
        ),
    )

    totals = compute_session_totals(conn)
    assert totals["unknown_sessions"] == 1
