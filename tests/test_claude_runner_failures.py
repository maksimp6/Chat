"""Independent counterexamples for lost turns and unsafe native dispatch recovery."""

import json
import subprocess

import pytest

from tests.test_development_session_contract import harness


def _history(call):
    return next(call["config"].rglob(f"{call['native_id']}.jsonl"))


def test_success_without_resumed_history_progress_cannot_acknowledge_input(harness):
    runner, reopen, spy, *_ = harness
    session = runner.open_session("no lost resumed turns")
    runner.enqueue(session, "first", "Initial instruction")
    runner.run_pending(session)
    spy.write_history = False
    runner.enqueue(session, "second", "This new turn must be persisted")
    result = runner.run_pending(session)
    assert result["status"] == "blocked"
    state = reopen().get_state(session)
    assert state["completed_event_ids"] == ["first"]
    assert state["queue"][0]["event_id"] == "second"
    reopen().run_pending(session)
    assert len(spy.calls) == 2


@pytest.mark.parametrize(
    "bad_history",
    [
        b"not JSON at all\n",
        b"[]\n",
        b'{"type":"user","sessionId":"dc462c2c-bf29-4cce-8b18-6ba331061f09"}\n',
    ],
)
def test_malformed_or_foreign_native_history_cannot_confirm_a_success(
    harness, monkeypatch, bad_history
):
    runner, reopen, spy, *_ = harness
    session = runner.open_session("validate persisted history ownership")
    runner.enqueue(session, "first", "Persist this instruction")

    def malformed(argv, **kwargs):
        completed = spy(argv, **kwargs)
        _history(spy.calls[-1]).write_bytes(bad_history)
        return completed

    monkeypatch.setattr(subprocess, "run", malformed)
    assert runner.run_pending(session)["status"] == "blocked"
    assert reopen().get_state(session)["completed_event_ids"] == []
    assert reopen().get_state(session)["queue"][0]["event_id"] == "first"
    reopen().run_pending(session)
    assert len(spy.calls) == 1


def test_compaction_can_replace_history_while_confirming_new_owned_turn(harness, monkeypatch):
    runner, _, spy, *_ = harness
    session = runner.open_session("preserve the durable mission after compaction")
    runner.enqueue(session, "first", "Initial turn")
    runner.run_pending(session)
    runner.enqueue(session, "second", "New owned turn after compaction")

    def compacted(argv, **kwargs):
        completed = spy(argv, **kwargs)
        call = spy.calls[-1]
        records = [
            {
                "type": "system",
                "subtype": "compact_boundary",
                "sessionId": call["native_id"],
                "opaqueMetadata": {"future": "preserve"},
            },
            {
                "type": "user",
                "sessionId": call["native_id"],
                "message": {"role": "user", "content": call["prompt"]},
            },
            {
                "type": "assistant",
                "sessionId": call["native_id"],
                "message": {"role": "assistant", "content": "Saved compacted turn."},
            },
        ]
        _history(call).write_text(
            "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
        )
        return completed

    monkeypatch.setattr(subprocess, "run", compacted)
    assert runner.run_pending(session)["status"] == "sleeping"
    assert runner.get_state(session)["completed_event_ids"] == ["first", "second"]
    assert runner.get_state(session)["goal"] == "preserve the durable mission after compaction"
    assert b"opaqueMetadata" in _history(spy.calls[-1]).read_bytes()


def test_metadata_only_history_change_does_not_acknowledge_new_owned_turn(harness, monkeypatch):
    runner, reopen, spy, *_ = harness
    session = runner.open_session("a changed digest alone is not a completed turn")
    runner.enqueue(session, "first", "Initial completed turn")
    runner.run_pending(session)
    spy.write_history = False
    runner.enqueue(session, "second", "Newest instruction was never persisted")

    def metadata_only(argv, **kwargs):
        completed = spy(argv, **kwargs)
        call = spy.calls[-1]
        with _history(call).open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps(
                    {
                        "type": "system",
                        "sessionId": call["native_id"],
                        "subtype": "metadata",
                        "opaqueMetadata": "new",
                    }
                )
                + "\n"
            )
        return completed

    monkeypatch.setattr(subprocess, "run", metadata_only)
    assert runner.run_pending(session)["status"] == "blocked"
    assert reopen().get_state(session)["completed_event_ids"] == ["first"]
    assert reopen().get_state(session)["queue"][0]["event_id"] == "second"
    reopen().run_pending(session)
    assert len(spy.calls) == 2


@pytest.mark.parametrize(
    "mutation", ["malformed", "native-id", "error-status", "error-flag", "nontext-answer"]
)
def test_unconfirmed_native_result_blocks_without_consuming_or_replaying(
    harness, monkeypatch, mutation
):
    runner, reopen, spy, *_ = harness
    session = runner.open_session("confirm native result before delivery acknowledgement")
    runner.enqueue(session, "first", "Keep this command on any ambiguous result")

    def unconfirmed(argv, **kwargs):
        completed = spy(argv, **kwargs)
        payload = json.loads(completed.stdout)
        if mutation == "malformed":
            completed.stdout = "not JSON"
        else:
            if mutation == "native-id":
                payload["session_id"] = "dc462c2c-bf29-4cce-8b18-6ba331061f09"
            elif mutation == "error-status":
                payload["subtype"] = "error_during_execution"
            elif mutation == "error-flag":
                payload["is_error"] = True
            else:
                payload["result"] = {"private": "nontext output"}
            completed.stdout = json.dumps(payload)
        return completed

    monkeypatch.setattr(subprocess, "run", unconfirmed)
    assert runner.run_pending(session)["reason"] == "provider_outcome_unknown"
    assert reopen().get_state(session)["queue"][0]["event_id"] == "first"
    reopen().run_pending(session)
    assert len(spy.calls) == 1


@pytest.mark.parametrize(
    "error",
    [
        subprocess.TimeoutExpired("claude", 120, output="synthetic-secret"),
        OSError("synthetic-secret"),
    ],
)
def test_unknown_subprocess_exception_is_redacted_and_not_replayed(harness, monkeypatch, error):
    runner, reopen, spy, *_ = harness
    session = runner.open_session("unknown process outcome")
    runner.enqueue(session, "first", "Keep the active command")

    attempts = []

    def failed_spawn(*_args, **_kwargs):
        attempts.append(True)
        raise error

    monkeypatch.setattr(subprocess, "run", failed_spawn)
    result = runner.run_pending(session)
    assert result["reason"] == "provider_outcome_unknown"
    assert "synthetic-secret" not in json.dumps(result)
    assert reopen().get_state(session)["queue"][0]["event_id"] == "first"
    reopen().run_pending(session)
    assert len(attempts) == 1
    assert spy.calls == []
