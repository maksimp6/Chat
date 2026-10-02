"""Independent guard tests for public runner input and durable recovery boundaries.

The native CLI fixture is synthetic. These tests never call a paid provider and
do not assert how the worker internally implements its locks or transcript data.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_development_session_contract import harness


@pytest.mark.parametrize("goal", [None, False, "", "   \n", "g" * (128 * 1024 + 1)])
def test_invalid_goal_cannot_create_a_session_or_start_native_work(harness, goal):
    runner, _, spy, _, _, state_dir = harness
    with pytest.raises(ValueError):
        runner.open_session(goal)
    assert list((state_dir / "sessions").glob("*.json")) == []
    assert spy.calls == []


@pytest.mark.parametrize(
    "item",
    [
        None,
        {"type": "issue", "id": "703", "extra": "untrusted"},
        {"type": "deployment", "id": "703"},
        {"type": "issue", "id": 703},
        {"type": "issue", "id": "0"},
        {"type": "issue", "id": "../703"},
        {"type": "pr", "id": "1" * 17},
    ],
)
def test_invalid_work_item_cannot_attach_untrusted_identity(harness, item):
    runner, _, spy, _, _, state_dir = harness
    with pytest.raises(ValueError):
        runner.open_session("validated work item", work_items=[item])
    assert list((state_dir / "sessions").glob("*.json")) == []
    assert spy.calls == []


def test_reopen_merges_unique_work_items_without_restarting_the_session(harness):
    runner, reopen, spy, *_ = harness
    issue = {"type": "issue", "id": "703"}
    pull_request = {"type": "pr", "id": "716"}
    session = runner.open_session("one goal and its linked work", work_items=[issue, issue])
    assert (
        reopen().open_session("one goal and its linked work", work_items=[pull_request, issue])
        == session
    )
    assert runner.get_state(session)["work_items"] == [issue, pull_request]
    assert spy.calls == []


@pytest.mark.parametrize(
    "session", [None, 17, "../private", "not-a-uuid", "DC462C2C-BF29-4CCE-8B18-6BA331061F09"]
)
def test_noncanonical_session_identity_is_rejected_before_access(harness, session):
    runner, _, spy, *_ = harness
    with pytest.raises(ValueError):
        runner.get_state(session)
    with pytest.raises(ValueError):
        runner.enqueue(session, "comment", "Do not access a different session")
    assert spy.calls == []


@pytest.mark.parametrize(
    "event_id,message",
    [
        (None, "valid"),
        ("", "valid"),
        ("e" * 257, "valid"),
        ("event", None),
        ("event", "   \n"),
        ("event", "m" * (128 * 1024 + 1)),
    ],
)
def test_invalid_event_does_not_modify_a_durable_queue(harness, event_id, message):
    runner, reopen, spy, *_ = harness
    session = runner.open_session("input validation")
    with pytest.raises(ValueError):
        runner.enqueue(session, event_id, message)
    state = reopen().get_state(session)
    assert state["queue"] == []
    assert state["received_event_ids"] == []
    assert spy.calls == []


@pytest.mark.parametrize("authorized", [None, 1, "true", {}])
def test_only_explicit_boolean_authorization_can_enqueue(harness, authorized):
    runner, _, spy, *_ = harness
    session = runner.open_session("strict dispatch authorization")
    assert (
        runner.enqueue(session, "outsider", "An untrusted instruction", authorized=authorized)
        is False
    )
    runner.run_pending(session)
    assert spy.calls == []
    assert runner.get_state(session)["queue"] == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema", 999),
        ("session_id", "dc462c2c-bf29-4cce-8b18-6ba331061f09"),
        ("role", "owner-merge-deploy"),
    ],
)
def test_corrupted_saved_identity_or_role_is_never_dispatched(harness, field, value):
    runner, reopen, spy, _, _, state_dir = harness
    session = runner.open_session("trusted durable identity")
    runner.enqueue(session, "pending", "Wait for a trusted worker")
    path = state_dir / "sessions" / f"{session}.json"
    saved = json.loads(path.read_text(encoding="utf-8"))
    saved[field] = value
    path.write_text(json.dumps(saved), encoding="utf-8")
    with pytest.raises(ValueError):
        reopen().run_pending(session)
    assert spy.calls == []


def test_active_turn_cannot_be_closed_but_finishes_normally(harness):
    runner, reopen, spy, *_ = harness
    session = runner.open_session("close only at a settled boundary")
    runner.enqueue(session, "first", "Finish this native turn")

    def attempt_close(_call):
        with pytest.raises(RuntimeError, match="active|running|boundary"):
            reopen().close(session)

    spy.on_turn = attempt_close
    assert runner.run_pending(session)["status"] == "sleeping"
    assert runner.get_state(session)["completed_event_ids"] == ["first"]
    assert len(spy.calls) == 1


def test_same_role_preserves_epoch_and_closed_session_rejects_role_change(harness):
    runner, reopen, spy, *_ = harness
    session = runner.open_session("role transition boundaries")
    runner.switch_role(session, "security-reviewer")
    first_epoch = runner.get_state(session)["role_epoch"]
    reopen().switch_role(session, "security-reviewer")
    assert runner.get_state(session)["role_epoch"] == first_epoch
    runner.close(session)
    with pytest.raises(RuntimeError, match="closed"):
        reopen().switch_role(session, "dialogue")
    state = runner.get_state(session)
    assert state["status"] == "closed"
    assert state["role"] == "security-reviewer"
    assert state["role_epoch"] == first_epoch
    assert spy.calls == []


def test_one_wake_is_bounded_and_remaining_comments_keep_native_identity(harness):
    runner, reopen, spy, *_ = harness
    session = runner.open_session("bounded backlog processing")
    events = [f"comment-{number}" for number in range(11)]
    for event in events:
        runner.enqueue(session, event, f"Instruction for {event}")
    first = runner.run_pending(session)
    assert len(spy.calls) == 8
    assert first["status"] == "sleeping"
    assert first["reason"] == "pending_comments"
    assert [item["event_id"] for item in first["queue"]] == events[8:]
    assert first["completed_event_ids"] == events[:8]
    final = reopen().run_pending(session)
    assert final["completed_event_ids"] == events
    assert final["queue"] == []
    assert final["reason"] == "awaiting_comment"
    assert len({call["native_id"] for call in spy.calls}) == 1
    assert len(spy.calls) == 11


@pytest.mark.parametrize(
    "unsafe_history", ["history-symlink", "projects-symlink", "multiple-histories", "oversize"]
)
def test_unsafe_native_history_cannot_acknowledge_or_replay_a_turn(
    harness, monkeypatch, unsafe_history
):
    runner, reopen, spy, _, config, state_dir = harness
    session = runner.open_session("native storage trust boundary")
    runner.enqueue(session, "first", "Persist a trustworthy native history")

    def unsafe(argv, **kwargs):
        completed = spy(argv, **kwargs)
        history = next(config.rglob(f"{spy.calls[-1]['native_id']}.jsonl"))
        if unsafe_history == "history-symlink":
            target = state_dir.parent / "outside-history.jsonl"
            history.rename(target)
            history.symlink_to(target)
        elif unsafe_history == "projects-symlink":
            projects = config / "projects"
            target = state_dir.parent / "outside-projects"
            projects.rename(target)
            projects.symlink_to(target, target_is_directory=True)
        elif unsafe_history == "multiple-histories":
            duplicate = config / "projects" / "another-project" / history.name
            duplicate.parent.mkdir()
            shutil.copyfile(history, duplicate)
        else:
            with history.open("r+b") as stream:
                stream.truncate(32 * 1024 * 1024 + 1)
        return completed

    monkeypatch.setattr(subprocess, "run", unsafe)
    assert runner.run_pending(session)["status"] == "blocked"
    state = reopen().get_state(session)
    assert state["completed_event_ids"] == []
    assert state["queue"][0]["event_id"] == "first"
    reopen().run_pending(session)
    assert len(spy.calls) == 1


@pytest.mark.parametrize("corruption", ["contents", "symlink"])
def test_untrusted_private_snapshot_blocks_before_native_resume(harness, corruption):
    runner, reopen, spy, _, config, state_dir = harness
    session = runner.open_session("verify a recovered transcript")
    runner.enqueue(session, "first", "Save this durable instruction")
    runner.run_pending(session)
    snapshot = next((state_dir / "snapshots" / session).glob("*.jsonl"))
    if corruption == "contents":
        snapshot.write_text("untrusted snapshot contents", encoding="utf-8")
    else:
        target = state_dir.parent / "outside-snapshot.jsonl"
        snapshot.rename(target)
        snapshot.symlink_to(target)
    shutil.rmtree(config)
    resumed = reopen()
    resumed.enqueue(session, "second", "Recover safely before executing this")
    result = resumed.run_pending(session)
    assert result["status"] == "blocked"
    assert result["reason"] == "missing_native_history"
    assert result["completed_event_ids"] == ["first"]
    assert result["queue"][0]["event_id"] == "second"
    assert len(spy.calls) == 1


@pytest.mark.parametrize("untrusted_cost", [True, "0.25", {}, -0.01, float("nan"), float("inf")])
def test_reported_usage_and_cost_drop_untrusted_nonfinite_or_negative_values(
    harness, monkeypatch, untrusted_cost
):
    runner, _, spy, *_ = harness
    session = runner.open_session("trusted billing metadata")
    runner.enqueue(session, "first", "A normal turn with untrusted metadata")

    def reported(argv, **kwargs):
        completed = spy(argv, **kwargs)
        payload = json.loads(completed.stdout)
        payload["usage"] = {
            "input_tokens": 20,
            "output_tokens": 4,
            "true_is_not_a_token_count": True,
            "negative": -1,
            "nan": float("nan"),
            "infinity": float("inf"),
            "text": "private-metadata",
            "nested": {"private": "untrusted"},
        }
        payload["total_cost_usd"] = untrusted_cost
        payload["arbitrary_private_data"] = "must not escape the provider boundary"
        completed.stdout = json.dumps(payload)
        return completed

    monkeypatch.setattr(subprocess, "run", reported)
    assert runner.run_pending(session)["status"] == "sleeping"
    results = runner.get_results(session)
    assert results[0]["usage"] == {"input_tokens": 20, "output_tokens": 4}
    assert results[0]["total_cost_usd"] is None
    assert "private-metadata" not in json.dumps(results)
    assert "arbitrary_private_data" not in results[0]


def test_fresh_worker_blocks_stale_inflight_instead_of_replaying_paid_work(harness):
    runner, reopen, spy, workspace, config, state_dir = harness
    session = runner.open_session("do not replay a crashed provider turn")
    runner.enqueue(session, "first", "A command that may already have run")
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
    recovered = reopen()
    state = recovered.run_pending(session)
    assert state["status"] == "blocked"
    assert state["reason"] == "provider_outcome_unknown"
    assert state["completed_event_ids"] == []
    assert state["queue"][0]["event_id"] == "first"
    recovered.run_pending(session)
    assert spy.calls == []
    with pytest.raises(RuntimeError, match="active|running|boundary"):
        recovered.close(session)
    with pytest.raises(RuntimeError, match="active|running|boundary"):
        recovered.switch_role(session, "security-reviewer")
