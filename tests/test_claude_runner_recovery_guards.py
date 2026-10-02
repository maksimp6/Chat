"""Public recovery behavior at native, persistence and filesystem fault boundaries."""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_claude_checkpoint_validation import _assert_restore_rejected, checkpoint_harness
from tests.test_development_session_contract import harness


def _saved_state(state_dir, session):
    path = state_dir / "sessions" / f"{session}.json"
    return path, json.loads(path.read_text(encoding="utf-8"))


def test_owned_native_text_blocks_and_opaque_metadata_survive_checkpoint(harness, monkeypatch):
    runner, _, spy, _, config, _ = harness
    session = runner.open_session("Preserve future transcript metadata")
    runner.enqueue(session, "first", "This owned text block is the completed instruction")

    def block_content(argv, **kwargs):
        completed = spy(argv, **kwargs)
        call = spy.calls[-1]
        history = next(config.rglob(f"{call['native_id']}.jsonl"))
        records = [json.loads(line) for line in history.read_text(encoding="utf-8").splitlines()]
        records[0]["message"]["content"] = [
            {"type": "opaque_future_data", "payload": "preserve-this-block"},
            {"type": "text", "text": call["prompt"]},
        ]
        history.write_text(
            " \n"
            + json.dumps({"type": "future_metadata", "opaque": "preserve-this-record"})
            + "\n"
            + "".join(json.dumps(record) + "\n" for record in records),
            encoding="utf-8",
        )
        return completed

    monkeypatch.setattr(subprocess, "run", block_content)
    assert runner.run_pending(session)["completed_event_ids"] == ["first"]
    transcript = runner.export_checkpoint(session)["transcript"]
    assert b"preserve-this-block" in transcript
    assert b"preserve-this-record" in transcript
    assert len(spy.calls) == 1


@pytest.mark.parametrize(
    "mutation",
    [
        "missing-work-item-id",
        "missing-snapshot",
        "snapshot-extra-authority",
        "malformed-integrity-digest",
    ],
)
def test_incomplete_recovery_evidence_cannot_create_live_state(checkpoint_harness, mutation):
    record = copy.deepcopy(checkpoint_harness[3])
    state = record["state"]
    if mutation == "missing-work-item-id":
        state["work_items"][0].pop("id")
    elif mutation == "missing-snapshot":
        state["snapshot"] = None
    elif mutation == "snapshot-extra-authority":
        state["snapshot"]["hooks"] = ["execute-on-resume"]
    else:
        state["snapshot"]["sha256"] = "not-a-digest"
    _assert_restore_rejected(checkpoint_harness, record)


def test_valid_individual_messages_cannot_exceed_aggregate_checkpoint_bound(checkpoint_harness):
    record = copy.deepcopy(checkpoint_harness[3])
    state = record["state"]
    message = "m" * (128 * 1024)
    state["queue"] = [
        {"event_id": f"pending-{number}", "message": message} for number in range(257)
    ]
    state["received_event_ids"] = state["completed_event_ids"] + [
        item["event_id"] for item in state["queue"]
    ]
    _assert_restore_rejected(checkpoint_harness, record)


def test_workspace_file_cannot_be_used_as_a_native_working_directory(harness):
    from agent_office.claude_runner import PersistentClaudeRunner

    _, _, spy, workspace, _, state_dir = harness
    workspace_file = workspace / "ordinary-file"
    workspace_file.write_text("This is not a directory", encoding="utf-8")
    destination_state = state_dir.parent / "invalid-workspace-state"
    with pytest.raises(ValueError, match="workspace"):
        PersistentClaudeRunner(
            state_dir=destination_state,
            workspace=workspace_file,
            claude_config_dir=state_dir.parent / "invalid-workspace-config",
        )
    assert not destination_state.exists()
    assert spy.calls == []


def test_configuration_directory_replaced_during_creation_blocks_before_dispatch(
    harness, monkeypatch
):
    runner, reopen, spy, _, config, _ = harness
    session = runner.open_session("A raced private directory cannot launch Claude")
    runner.enqueue(session, "first", "Keep this instruction until recovery is trusted")
    original_mkdir = Path.mkdir

    def replace_created_directory(path, *args, **kwargs):
        result = original_mkdir(path, *args, **kwargs)
        if path == config:
            path.rmdir()
            path.write_text("Replaced by another filesystem actor", encoding="utf-8")
        return result

    monkeypatch.setattr(Path, "mkdir", replace_created_directory)
    result = runner.run_pending(session)
    assert result["status"] == "blocked"
    assert result["reason"] == "missing_native_history"
    assert result["completed_event_ids"] == []
    assert result["queue"][0]["event_id"] == "first"
    reopen().run_pending(session)
    assert spy.calls == []


def test_failed_atomic_replacement_preserves_original_state_and_removes_temporary_file(
    harness, monkeypatch
):
    runner, reopen, spy, _, _, state_dir = harness
    session = runner.open_session("Durable state must survive a failed replacement")
    state_path, _ = _saved_state(state_dir, session)
    original_state = state_path.read_bytes()
    original_replace = os.replace

    def fail_state_replace(source, destination):
        if Path(destination) == state_path:
            raise OSError("synthetic storage replacement failed")
        return original_replace(source, destination)

    monkeypatch.setattr(os, "replace", fail_state_replace)
    with pytest.raises(OSError):
        runner.enqueue(session, "first", "Do not partially commit this command")
    assert state_path.read_bytes() == original_state
    assert reopen().get_state(session)["queue"] == []
    assert list(state_path.parent.glob(".pending-*")) == []
    assert spy.calls == []


def test_native_history_growth_between_metadata_check_and_read_is_bounded(harness, monkeypatch):
    runner, reopen, spy, _, config, _ = harness
    session = runner.open_session("A growing native transcript must not bypass its bound")
    runner.enqueue(session, "first", "A command requiring a bounded private transcript")
    original_fstat = os.fstat
    grew = []

    def grow_history_after_stat(descriptor):
        metadata = original_fstat(descriptor)
        opened = Path(os.readlink(f"/proc/self/fd/{descriptor}"))
        if not grew and opened.suffix == ".jsonl" and config in opened.parents:
            with opened.open("r+b") as stream:
                stream.truncate(32 * 1024 * 1024 + 1)
            grew.append(opened)
        return metadata

    monkeypatch.setattr(os, "fstat", grow_history_after_stat)
    result = runner.run_pending(session)
    assert grew, "Exercise real file growth after the metadata size was checked"
    assert result["status"] == "blocked"
    assert result["completed_event_ids"] == []
    assert result["queue"][0]["event_id"] == "first"
    reopen().run_pending(session)
    assert len(spy.calls) == 1


def test_crashed_running_state_cannot_export_and_restores_as_ambiguous(harness):
    from agent_office.claude_runner import PersistentClaudeRunner

    source, _, spy, workspace, config, state_dir = harness
    session = source.open_session("Preserve a turn that crashed at the provider boundary")
    source.enqueue(session, "first", "This instruction may already have reached Claude")
    record = source.export_checkpoint(session)
    script = (
        "import os,subprocess,sys; from pathlib import Path; "
        "from agent_office.claude_runner import PersistentClaudeRunner; "
        "subprocess.run=lambda *args,**kwargs: os._exit(17); "
        "runner=PersistentClaudeRunner(state_dir=Path(sys.argv[1]), "
        "workspace=Path(sys.argv[2]),claude_config_dir=Path(sys.argv[3])); "
        "runner.run_pending(sys.argv[4])"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(state_dir), str(workspace), str(config), session],
        cwd=Path(__file__).resolve().parents[1],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    stdout, stderr = process.communicate(timeout=10)
    assert process.returncode == 17, (stdout, stderr)
    with pytest.raises(RuntimeError, match="active|running|boundary"):
        source.export_checkpoint(session)
    _, record["state"] = _saved_state(state_dir, session)
    destination = PersistentClaudeRunner(
        state_dir=state_dir.parent / "crashed-restored",
        workspace=workspace,
        claude_config_dir=state_dir.parent / "crashed-config",
    )
    assert destination.restore_checkpoint(**record) == session
    result = destination.run_pending(session)
    assert result["status"] == "blocked"
    assert result["reason"] == "provider_outcome_unknown"
    assert result["queue"][0]["event_id"] == "first"
    assert result["completed_event_ids"] == []
    assert spy.calls == []


def test_corrupted_saved_snapshot_generation_is_not_exported(harness):
    runner, _, spy, _, _, state_dir = harness
    session = runner.open_session("Export only trusted recovery evidence")
    runner.enqueue(session, "first", "Persist an owned transcript")
    runner.run_pending(session)
    state_path, state = _saved_state(state_dir, session)
    state["snapshot"]["generation"] = "../../credentials"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(ValueError):
        runner.export_checkpoint(session)
    assert len(spy.calls) == 1


def test_restore_cannot_replace_an_active_native_turn(harness):
    runner, _, spy, *_ = harness
    session = runner.open_session("An active turn owns its state until completion")
    runner.enqueue(session, "first", "Persist the original native turn")
    runner.run_pending(session)
    older = runner.export_checkpoint(session)
    runner.enqueue(session, "second", "Do not replace this in-progress input")

    def attempt_restore(_call):
        with pytest.raises(RuntimeError, match="active|running|boundary"):
            runner.restore_checkpoint(**older)

    spy.on_turn = attempt_restore
    assert runner.run_pending(session)["completed_event_ids"] == ["first", "second"]
    assert len(spy.calls) == 2


def test_restoring_the_same_checkpoint_twice_does_not_replay_completed_input(checkpoint_harness):
    _, session, spy, record, destination, *_ = checkpoint_harness
    assert destination.restore_checkpoint(**record) == session
    assert destination.restore_checkpoint(**record) == session
    assert len(spy.calls) == 1
    state = destination.run_pending(session)
    assert state["completed_event_ids"] == [
        "completed-comment",
        "pending-comment-1",
        "pending-comment-2",
    ]
    assert state["queue"] == []
    assert len(spy.calls) == 3


def test_unrelated_symlink_project_is_not_used_as_native_history(harness, monkeypatch):
    runner, _, spy, _, config, state_dir = harness
    session = runner.open_session("Restore only the owned native project")
    runner.enqueue(session, "first", "Keep foreign symlink histories out of the checkpoint")

    def foreign_project(argv, **kwargs):
        completed = spy(argv, **kwargs)
        external = state_dir.parent / "external-native-project"
        external.mkdir()
        (external / f"{spy.calls[-1]['native_id']}.jsonl").write_text(
            "foreign-private-history", encoding="utf-8"
        )
        (config / "projects" / "foreign-project-link").symlink_to(
            external, target_is_directory=True
        )
        return completed

    monkeypatch.setattr(subprocess, "run", foreign_project)
    assert runner.run_pending(session)["completed_event_ids"] == ["first"]
    assert b"foreign-private-history" not in runner.export_checkpoint(session)["transcript"]
    assert len(spy.calls) == 1


def test_empty_successful_native_history_does_not_acknowledge_or_replay(harness, monkeypatch):
    runner, reopen, spy, _, config, _ = harness
    session = runner.open_session("An empty file does not prove a completed native turn")
    runner.enqueue(session, "first", "Retain this unconfirmed instruction")

    def empty_history(argv, **kwargs):
        completed = spy(argv, **kwargs)
        next(config.rglob(f"{spy.calls[-1]['native_id']}.jsonl")).write_bytes(b"")
        return completed

    monkeypatch.setattr(subprocess, "run", empty_history)
    assert runner.run_pending(session)["status"] == "blocked"
    assert runner.get_state(session)["queue"][0]["event_id"] == "first"
    assert runner.get_state(session)["completed_event_ids"] == []
    reopen().run_pending(session)
    assert len(spy.calls) == 1


@pytest.mark.parametrize("mutation", ["missing-snapshot", "unsafe-project"])
def test_corrupted_durable_resume_metadata_blocks_before_the_next_provider_turn(harness, mutation):
    runner, reopen, spy, _, _, state_dir = harness
    session = runner.open_session("A resumed turn requires trustworthy native history")
    runner.enqueue(session, "first", "Persist one completed turn")
    runner.run_pending(session)
    runner.enqueue(session, "second", "Preserve this pending instruction")
    state_path, state = _saved_state(state_dir, session)
    if mutation == "missing-snapshot":
        state["snapshot"] = None
    else:
        state["snapshot"]["project"] = "../untrusted-project"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    result = reopen().run_pending(session)
    assert result["status"] == "blocked"
    assert result["reason"] == "missing_native_history"
    assert result["completed_event_ids"] == ["first"]
    assert result["queue"][0]["event_id"] == "second"
    assert len(spy.calls) == 1
