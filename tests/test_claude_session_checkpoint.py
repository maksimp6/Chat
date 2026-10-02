"""Issue #703 portable private session snapshot, without general file archives.

Synthetic transcript fixtures verify byte persistence and authorization/state
boundaries. Genuine CLI resume compatibility is a separate live acceptance gate.
"""

from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path

import pytest

from tests.test_development_session_contract import NativeClaudeSpy


@pytest.fixture
def snapshot_harness(tmp_path, monkeypatch):
    from agent_office.claude_runner import PersistentClaudeRunner

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = tmp_path / "source-config"
    source = PersistentClaudeRunner(
        state_dir=tmp_path / "source-state", workspace=workspace, claude_config_dir=config
    )
    spy = NativeClaudeSpy()
    monkeypatch.setattr(subprocess, "run", spy)
    session = source.open_session(
        "Preserve the canonical goal", work_items=({"type": "issue", "id": "703"},)
    )
    source.enqueue(session, "issue-comment:1", "Remember owner approval boundaries")
    source.run_pending(session)

    def destination():
        return PersistentClaudeRunner(
            state_dir=tmp_path / "destination-state",
            workspace=workspace,
            claude_config_dir=tmp_path / "destination-config",
        )

    return source, session, spy, destination, config, tmp_path


def test_portable_snapshot_restores_actual_history_queue_role_and_dedup(snapshot_harness):
    source, session, spy, destination, *_ = snapshot_harness
    source.switch_role(session, "security-reviewer")
    source.enqueue(session, "issue-comment:2", "Keep the newest undelivered comment")
    record = source.export_checkpoint(session)
    assert set(record) == {"state", "native_session_id", "transcript"}
    assert record["native_session_id"] == spy.calls[0]["native_id"]
    assert isinstance(record["transcript"], bytes)
    restored = destination()
    assert restored.restore_checkpoint(**record) == session
    assert restored.get_state(session)["role"] == "security-reviewer"
    assert restored.get_state(session)["queue"] == [
        {"event_id": "issue-comment:2", "message": "Keep the newest undelivered comment"}
    ]
    assert restored.enqueue(session, "issue-comment:1", "Duplicate already completed") is False
    restored.run_pending(session)
    assert len(spy.calls) == 2
    assert "Remember owner approval boundaries" in spy.calls[-1]["history_before"]
    assert "Keep the newest undelivered comment" in spy.calls[-1]["prompt"]
    assert spy.calls[-1]["native_id"] == record["native_session_id"]


def test_export_contains_only_selected_session_not_credentials_settings_or_hooks(snapshot_harness):
    source, session, _, _, config, _ = snapshot_harness
    foreign_marker = "foreign-private-file-must-not-be-exported"
    for name in (
        ".credentials.json",
        "settings.json",
        "hooks.sh",
        "projects/foreign/37ce5742-3563-4ff2-9d16-2bcdb0782d0f.jsonl",
    ):
        path = config / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(foreign_marker, encoding="utf-8")
    record = source.export_checkpoint(session)
    assert foreign_marker not in str(record)
    assert set(record) == {"state", "native_session_id", "transcript"}


def test_exported_state_is_detached_from_live_registry(snapshot_harness):
    source, session, *_ = snapshot_harness
    record = source.export_checkpoint(session)
    record["state"]["goal"] = "mutated caller copy"
    assert source.get_state(session)["goal"] == "Preserve the canonical goal"


def test_export_requires_native_turn_boundary(snapshot_harness):
    source, session, spy, *_ = snapshot_harness
    source.enqueue(session, "issue-comment:2", "One more turn")

    def attempt_active_export(_call):
        with pytest.raises(RuntimeError, match="active|running|boundary"):
            source.export_checkpoint(session)

    spy.on_turn = attempt_active_export
    source.run_pending(session)


@pytest.mark.parametrize(
    "mutation", ["role", "capabilities", "native_session_id", "project", "generation"]
)
def test_restore_rejects_unknown_authority_ids_or_snapshot_path(snapshot_harness, mutation):
    source, session, spy, destination, *_ = snapshot_harness
    record = copy.deepcopy(source.export_checkpoint(session))
    if mutation == "role":
        record["state"]["role"] = "owner-merge-deploy"
    elif mutation == "capabilities":
        record["state"]["capabilities"] = ["Write", "deploy"]
    elif mutation == "native_session_id":
        record["native_session_id"] = "37ce5742-3563-4ff2-9d16-2bcdb0782d0f"
    elif mutation == "project":
        record["state"]["snapshot"]["project"] = "../../credentials"
    else:
        record["state"]["snapshot"]["generation"] = "../../outside-state"
    with pytest.raises(ValueError):
        destination().restore_checkpoint(**record)
    assert len(spy.calls) == 1


def test_restore_checks_transcript_integrity_before_any_native_resume(snapshot_harness):
    source, session, spy, destination, *_ = snapshot_harness
    record = source.export_checkpoint(session)
    record["transcript"] += b"tampered transcript"
    with pytest.raises(ValueError):
        destination().restore_checkpoint(**record)
    assert len(spy.calls) == 1


def test_ambiguous_inflight_command_remains_blocked_after_host_restore(snapshot_harness):
    source, session, spy, destination, *_ = snapshot_harness
    spy.fail_once = True
    source.enqueue(session, "issue-comment:2", "Do not automatically replay this command")
    assert source.run_pending(session)["reason"] == "provider_outcome_unknown"
    record = source.export_checkpoint(session)
    restored = destination()
    restored.restore_checkpoint(**record)
    assert restored.get_state(session)["reason"] == "provider_outcome_unknown"
    assert restored.get_state(session)["queue"][0]["event_id"] == "issue-comment:2"
    restored.run_pending(session)
    assert len(spy.calls) == 2


def test_stale_local_snapshot_cannot_roll_back_completed_cursor(snapshot_harness):
    source, session, spy, _, *_ = snapshot_harness
    older = source.export_checkpoint(session)
    source.enqueue(session, "issue-comment:2", "Complete newer work")
    source.run_pending(session)
    with pytest.raises(ValueError, match="stale|rollback|checkpoint"):
        source.restore_checkpoint(**older)
    assert source.get_state(session)["completed_event_ids"] == [
        "issue-comment:1",
        "issue-comment:2",
    ]
    assert len(spy.calls) == 2


def test_restore_refuses_symlinked_configuration_destination(snapshot_harness):
    source, session, spy, destination, _, root = snapshot_harness
    record = source.export_checkpoint(session)
    outside = root / "outside"
    outside.mkdir()
    (root / "destination-config").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        destination().restore_checkpoint(**record)
    assert list(outside.iterdir()) == []
    assert len(spy.calls) == 1


def test_checkpoint_pack_round_trip_has_no_general_archive_paths(snapshot_harness):
    from agent_office.claude_checkpoint import pack_checkpoint, unpack_checkpoint

    source, session, *_ = snapshot_harness
    record = source.export_checkpoint(session)
    payload = pack_checkpoint(**record)
    assert unpack_checkpoint(payload) == record
    envelope = json.loads(payload)
    envelope["files"] = {"../../credentials.json": "must-never-restore"}
    with pytest.raises(ValueError, match="^checkpoint_invalid$"):
        unpack_checkpoint(json.dumps(envelope).encode("utf-8"))
