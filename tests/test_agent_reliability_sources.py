import json
import runpy
import sys

import pytest

from agent_office.reliability import evaluate_snapshot
from agent_office.reliability_sources import build_snapshot


def test_observer_snapshot_and_stall_are_real_evidence():
    observer = {
        "generated_at": "2026-09-30T07:17:00+00:00",
        "repo": "maksimp6/Chat",
        "findings": [
            {
                "severity": "medium",
                "kind": "maintainer_stall",
                "number": 548,
                "url": "https://github.com/maksimp6/Chat/pull/548",
            }
        ],
        "threads": [],
    }

    snapshot = build_snapshot(
        observer=observer,
        observer_ref="artifact:agent-observer",
        generated_at="2026-09-30T07:18:00+00:00",
    )

    assert snapshot["observer_last_success_at"] == observer["generated_at"]
    assert snapshot["maintainer_handoffs"] == [
        {"id": "pr:548", "observer_passes": 2, "outcome": "stalled"}
    ]
    result = evaluate_snapshot(snapshot)
    assert result["ready"] is False
    assert "maintainer_stall" in {item["code"] for item in result["findings"]}


def test_missing_observer_metadata_fails_closed():
    snapshot = build_snapshot(
        observer={"findings": [], "threads": []},
        observer_ref="artifact:broken-observer",
        generated_at="2026-09-30T07:18:00+00:00",
    )

    assert snapshot["decisions"][0]["status"] == "unknown"
    assert snapshot["observer_last_success_at"] is None


def test_blocked_readiness_is_a_valid_fail_closed_decision():
    readiness = {
        "ready": False,
        "repo": "maksimp6/Chat",
        "pr_number": 548,
        "head_sha": "abc123",
        "behind_by": 15,
        "blockers": [{"code": "behind_master", "detail": "head is behind"}],
    }

    snapshot = build_snapshot(
        readiness=[(readiness, "artifact:readiness-548")],
        generated_at="2026-09-30T07:18:00+00:00",
    )

    decision = snapshot["decisions"][0]
    assert decision["status"] == "valid"
    assert decision["kind"] == "merge_readiness"


def test_contradictory_ready_with_blockers_is_invalid():
    readiness = {
        "ready": True,
        "repo": "maksimp6/Chat",
        "pr_number": 1,
        "head_sha": "deadbeef",
        "blockers": [{"code": "review_threads"}],
    }

    snapshot = build_snapshot(
        readiness=[(readiness, "artifact:contradictory")],
        generated_at="2026-09-30T07:18:00+00:00",
    )

    assert snapshot["decisions"][0]["status"] == "invalid"


def test_trace_cache_retrieval_and_cost_waste_are_collected():
    trace = {
        "trace_id": "trace-1",
        "context_cache_operations": [
            {
                "status": "hit",
                "repository": "maksimp6/Chat",
                "work_item": "PR#591",
                "head_sha": "head-1",
            }
        ],
        "retrieval_operations": [
            {
                "status": "hit",
                "repository": "maksimp6/Chat",
                "work_item": "PR#591",
                "head_sha": "head-1",
            }
        ],
        "events": [
            {
                "type": "raw_context_read",
                "data": {
                    "repository": "maksimp6/Chat",
                    "work_item": "PR#591",
                    "head_sha": "head-1",
                },
            },
            {
                "type": "raw_context_read",
                "data": {
                    "repository": "maksimp6/Chat",
                    "work_item": "PR#591",
                    "head_sha": "head-1",
                },
            },
            {
                "type": "model_call",
                "data": {"tier": "strong", "evidence_version": "v1"},
            },
            {
                "type": "model_call",
                "data": {"tier": "strong", "evidence_version": "v1"},
            },
        ],
    }

    snapshot = build_snapshot(
        traces=[(trace, "trace-file:trace-1")],
        generated_at="2026-09-30T07:18:00+00:00",
    )

    assert [item["status"] for item in snapshot["decisions"]] == ["valid", "valid"]
    assert snapshot["duplicate_exact_context_reads"] == 1
    assert snapshot["strong_retries_without_new_evidence"] == 1


def test_approval_required_is_not_mistaken_for_approval_success():
    trace = {
        "trace_id": "trace-approval",
        "events": [
            {
                "type": "tool_approval",
                "data": {"phase": "approval_required"},
            }
        ],
    }

    snapshot = build_snapshot(
        traces=[(trace, "trace-file:approval")],
        generated_at="2026-09-30T07:18:00+00:00",
    )

    assert snapshot["decisions"][0]["kind"] == "approval_boundary"
    assert snapshot["decisions"][0]["status"] == "valid"


def test_unknown_approval_phase_does_not_become_success():
    trace = {
        "trace_id": "trace-approval",
        "events": [{"type": "tool_approval", "data": {"phase": "approved"}}],
    }

    snapshot = build_snapshot(
        traces=[(trace, "trace-file:approval")],
        generated_at="2026-09-30T07:18:00+00:00",
    )

    assert snapshot["decisions"][0]["status"] == "unknown"


def test_non_dict_observer_findings_and_trace_items_are_ignored():
    snapshot = build_snapshot(
        observer={
            "generated_at": "2026-09-30T07:17:00+00:00",
            "repo": "maksimp6/Chat",
            "findings": ["bad-finding"],
        },
        observer_ref="artifact:observer",
        traces=[
            (
                {
                    "trace_id": "trace-mixed",
                    "context_cache_operations": ["bad-cache"],
                    "retrieval_operations": ["bad-retrieval"],
                    "events": ["bad-event"],
                },
                "artifact:trace",
            )
        ],
        generated_at="2026-09-30T07:18:00+00:00",
    )

    assert len(snapshot["decisions"]) == 1
    assert snapshot["decisions"][0]["kind"] == "observer_snapshot"


def test_non_dict_event_payload_is_treated_as_empty():
    snapshot = build_snapshot(
        traces=[
            (
                {
                    "trace_id": "trace-payload",
                    "events": [{"type": "tool_approval", "data": "not-an-object"}],
                },
                "artifact:trace",
            )
        ],
        generated_at="2026-09-30T07:18:00+00:00",
    )

    assert snapshot["decisions"][0]["status"] == "unknown"


def test_invalid_cache_and_retrieval_records_are_invalid_decisions():
    snapshot = build_snapshot(
        traces=[
            (
                {
                    "trace_id": "trace-invalid",
                    "context_cache_operations": [{"status": "hit"}],
                    "retrieval_operations": [{"status": "mystery"}],
                },
                "artifact:trace",
            )
        ],
        generated_at="2026-09-30T07:18:00+00:00",
    )

    assert [item["status"] for item in snapshot["decisions"]] == ["invalid", "invalid"]


def test_cli_reads_real_files_and_writes_snapshot(tmp_path, monkeypatch):
    observer = tmp_path / "observer.json"
    readiness = tmp_path / "readiness.json"
    trace = tmp_path / "trace.json"
    output = tmp_path / "snapshot.json"

    observer.write_text(
        json.dumps(
            {
                "generated_at": "2026-09-30T07:17:00+00:00",
                "repo": "maksimp6/Chat",
                "findings": [],
                "threads": [],
            }
        ),
        encoding="utf-8",
    )
    readiness.write_text(
        json.dumps(
            {
                "ready": False,
                "repo": "maksimp6/Chat",
                "pr_number": 548,
                "head_sha": "abc123",
                "blockers": [{"code": "behind_master"}],
            }
        ),
        encoding="utf-8",
    )
    trace.write_text(json.dumps({"trace_id": "trace-cli"}), encoding="utf-8")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "agent-office-reliability-sources",
            "--observer",
            str(observer),
            "--readiness",
            str(readiness),
            "--trace",
            str(trace),
            "--json-out",
            str(output),
            "--pretty",
        ],
    )

    from agent_office import reliability_sources

    assert reliability_sources.main() == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["observer_last_success_at"] == "2026-09-30T07:17:00+00:00"
    assert any(item["kind"] == "merge_readiness" for item in payload["decisions"])


def test_cli_prints_snapshot_without_output_file(tmp_path, monkeypatch, capsys):
    readiness = tmp_path / "readiness.json"
    readiness.write_text(
        json.dumps(
            {
                "ready": True,
                "repo": "maksimp6/Chat",
                "pr_number": 593,
                "head_sha": "head-ok",
                "blockers": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["agent-office-reliability-sources", "--readiness", str(readiness)],
    )

    from agent_office import reliability_sources

    assert reliability_sources.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["decisions"][0]["status"] == "valid"


def test_json_loader_rejects_non_object_files(tmp_path, monkeypatch):
    observer = tmp_path / "observer.json"
    observer.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        ["agent-office-reliability-sources", "--observer", str(observer)],
    )

    from agent_office import reliability_sources

    with pytest.raises(ValueError, match="expected a JSON object"):
        reliability_sources.main()


def test_module_entrypoint_exits_successfully(tmp_path, monkeypatch):
    readiness = tmp_path / "readiness.json"
    readiness.write_text(
        json.dumps(
            {
                "ready": False,
                "repo": "maksimp6/Chat",
                "pr_number": 548,
                "head_sha": "abc123",
                "blockers": [{"code": "behind_master"}],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["agent-office-reliability-sources", "--readiness", str(readiness)],
    )

    with pytest.raises(SystemExit) as exc:
        runpy.run_module("agent_office.reliability_sources", run_name="__main__")

    assert exc.value.code == 0
