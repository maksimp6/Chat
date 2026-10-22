"""Real Kristall boundary, publication and integrity controls for large invocations."""

import base64
import json

import pytest

from agent_memory.client import close_memory_clients
from invocation import manager
from invocation.trace import create_invocation_trace
from kristall import KristallError
from kristall.store import MAX_JOURNAL_FRAME_BYTES
from scripts import logos_dialogue as dialogue
from storage.db import get_store, memory_file_path


def invocation():
    context = manager.create_invocation("large-session", "large-conversation")
    assert manager.start_invocation(context.invocation_id)
    return context


def large_trace(context):
    trace = create_invocation_trace(context)
    payload = {
        "tools": [{"name": f"tool-{index}", "description": "Ж" * 4000} for index in range(50)]
    }
    for step in range(1, 4):
        trace.add_api_request(payload, step_index=step)
        trace.add_event("api_request_sent", payload)
        trace.add_response({"id": f"response-{step}", "status": "completed"}, step_index=step)
    return trace


def raw_record(context):
    return get_store().get(manager._key(context.invocation_id))


def chunk_key(context, field, manifest, index=0):
    return (
        f"{manager._VALUE_CHUNK_PREFIX}{context.invocation_id}:{field}:"
        f"{manifest['generation']}:{index}"
    )


def test_trace_above_native_frame_limit_is_exact_after_durable_reopen():
    context = invocation()
    trace = large_trace(context).finalize()
    encoded = json.dumps(trace, ensure_ascii=False).encode()
    assert len(encoded) > MAX_JOURNAL_FRAME_BYTES
    # Negative control: the former single-record write reproduces KristallError.
    with (
        pytest.raises(KristallError, match="value text exceeds size limit"),
        get_store().transaction() as tx,
    ):
        record = tx.get(manager._key(context.invocation_id))
        record["trace"] = manager._sanitize_metadata(trace)
        tx.set(manager._key(context.invocation_id), record)
    assert manager.get_invocation(context.invocation_id)["trace"] == {}
    assert manager.persist_invocation_trace(context.invocation_id, trace)
    raw = raw_record(context)
    assert raw["trace"] is None and raw["trace_chunks"]["chunks"] > 1
    close_memory_clients()
    restored = manager.get_invocation(context.invocation_id)
    assert restored["trace"] == manager._sanitize_metadata(trace)
    assert "trace_chunks" not in restored
    assert all(
        len(line) <= MAX_JOURNAL_FRAME_BYTES
        for line in memory_file_path().read_bytes().splitlines(keepends=True)
    )


def test_small_trace_keeps_legacy_inline_shape_and_large_can_return_inline():
    context = invocation()
    assert manager.persist_invocation_trace(context.invocation_id, {"trace": "small"})
    assert raw_record(context)["trace"] == {"trace": "small"}
    assert "trace_chunks" not in raw_record(context)
    assert manager.persist_invocation_trace(
        context.invocation_id, {"data": "x" * MAX_JOURNAL_FRAME_BYTES}
    )
    assert "trace_chunks" in raw_record(context)
    assert manager.persist_invocation_trace(context.invocation_id, {"trace": "small again"})
    assert raw_record(context)["trace"] == {"trace": "small again"}
    assert "trace_chunks" not in raw_record(context)


def test_large_result_and_error_use_same_native_storage_without_clipping():
    context = invocation()
    result = {"reply": "Ё" * MAX_JOURNAL_FRAME_BYTES, "api_key": "must-not-persist"}
    assert manager.finish_invocation(context.invocation_id, result)
    close_memory_clients()
    loaded = manager.get_invocation(context.invocation_id)
    assert loaded["status"] == "completed"
    assert loaded["result"] == {"reply": result["reply"]}
    assert not manager.finish_invocation(context.invocation_id, result)
    failed = manager.create_invocation("failed-session", "failed-conversation")
    error = {"message": "e" * MAX_JOURNAL_FRAME_BYTES}
    assert manager.fail_invocation(failed.invocation_id, error)
    assert manager.get_invocation(failed.invocation_id)["error"] == error


@pytest.mark.parametrize("failure_stage", ["chunk", "manifest"])
def test_interrupted_trace_publication_keeps_prior_trace_authoritative(monkeypatch, failure_stage):
    context = invocation()
    assert manager.persist_invocation_trace(context.invocation_id, {"prior": True})
    engine = get_store()._engine
    encode = engine._encode_frame
    written_chunks = []

    def interrupt(changes):
        for change in changes:
            name = change["key"]
            if name.startswith(manager._VALUE_CHUNK_PREFIX):
                written_chunks.append(name)
                if failure_stage == "chunk" and len(written_chunks) == 2:
                    raise OSError("staged chunk interruption")
            if (
                failure_stage == "manifest"
                and name == manager._key(context.invocation_id)
                and "trace_chunks" in change.get("value", {})
            ):
                raise OSError("manifest publication interruption")
        return encode(changes)

    monkeypatch.setattr(engine, "_encode_frame", interrupt)
    with pytest.raises(OSError, match="interruption"):
        manager.persist_invocation_trace(
            context.invocation_id, {"next": "n" * MAX_JOURNAL_FRAME_BYTES}
        )
    assert written_chunks
    assert manager.get_invocation(context.invocation_id)["trace"] == {"prior": True}
    assert manager.get_invocation(context.invocation_id)["status"] == "running"
    monkeypatch.setattr(engine, "_encode_frame", encode)
    close_memory_clients()
    assert manager.get_invocation(context.invocation_id)["trace"] == {"prior": True}


def test_failed_result_manifest_does_not_publish_completed_status(monkeypatch):
    context = invocation()
    engine = get_store()._engine
    encode = engine._encode_frame

    def interrupt(changes):
        if any("result_chunks" in change.get("value", {}) for change in changes):
            raise OSError("completion publication interruption")
        return encode(changes)

    monkeypatch.setattr(engine, "_encode_frame", interrupt)
    with pytest.raises(OSError, match="publication interruption"):
        manager.finish_invocation(context.invocation_id, {"reply": "r" * MAX_JOURNAL_FRAME_BYTES})
    assert manager.get_invocation(context.invocation_id)["status"] == "running"
    assert manager.get_invocation(context.invocation_id)["result"] is None


@pytest.mark.parametrize("damage", ["missing", "digest", "length", "base64", "manifest"])
def test_chunk_corruption_is_fail_closed(damage):
    context = invocation()
    assert manager.persist_invocation_trace(
        context.invocation_id, {"data": "x" * MAX_JOURNAL_FRAME_BYTES}
    )
    record = raw_record(context)
    manifest = record["trace_chunks"]
    name = chunk_key(context, "trace", manifest)
    with get_store().transaction() as tx:
        if damage == "missing":
            tx.delete(name)
        elif damage == "manifest":
            record["trace_chunks"]["chunks"] = True
            tx.set(manager._key(context.invocation_id), record)
        else:
            value = tx.get(name)
            if damage == "digest":
                decoded = base64.b64decode(value["data"])
                value["data"] = base64.b64encode(b"!" + decoded[1:]).decode()
            elif damage == "length":
                value["data"] = base64.b64encode(b"short").decode()
            else:
                value["data"] = "!not base64!"
            tx.set(name, value)
    with pytest.raises(KristallError, match="invocation value"):
        manager.get_invocation(context.invocation_id)


def test_finisher_exports_completed_evidence_even_when_trace_persistence_fails(monkeypatch):
    context = invocation()
    trace = create_invocation_trace(context)
    trace.add_api_request({"model": "model"}, step_index=1)
    trace.track_tool_execution(
        "read_file", {}, lambda: {"success": True, "data": {}}, call_id="read-1"
    )
    trace.add_response(
        {
            "id": "provider-done",
            "status": "completed",
            "output": [{"type": "file_search_call", "id": "search-1", "status": "completed"}],
            "usage": {"input_tokens": 12, "output_tokens": 7, "total_tokens": 19},
        },
        step_index=1,
    )
    result = dialogue._new_result()
    result.update(
        status="completed",
        reply="actual completed answer",
        checkpoint={"state": "response_received", "reply_verified": False},
    )

    def fail(*args):
        raise KristallError("journal frame exceeds size limit")

    monkeypatch.setattr(dialogue, "persist_invocation_trace", fail)
    with pytest.raises(KristallError):
        dialogue._finish(context, trace, result)
    assert result["reply"] == "actual completed answer"
    assert result["generation_requests"] == 1
    assert result["tool_calls"] == [{"name": "read_file", "call_id": "read-1", "success": True}]
    assert result["hosted_tool_calls"][0]["id"] == "search-1"
    assert result["response_ids"] == ["provider-done"]
    assert result["usage"] == {"input_tokens": 12, "output_tokens": 7, "total_tokens": 19}
    assert result["persistence"]["stage"] == "trace"
    assert result["persistence"]["reason"] == "journal_frame_limit"
    assert result["checkpoint"]["reply_verified"] is False


def test_finisher_persists_full_large_trace_and_full_large_answer():
    context = invocation()
    trace = large_trace(context)
    result = dialogue._new_result()
    result.update(status="completed", reply="answer" * (MAX_JOURNAL_FRAME_BYTES // 4))
    dialogue._finish(context, trace, result)
    loaded = manager.get_invocation(context.invocation_id)
    assert loaded["status"] == "completed"
    assert loaded["result"]["reply"] == result["reply"]
    assert loaded["trace"] == manager._sanitize_metadata(trace.finalize())
    assert result["persistence"]["status"] == "stored"
    assert result["checkpoint"]["reply_verified"] is True
    assert loaded["result"]["checkpoint"]["reply_verified"] is True


def test_final_response_checkpoint_and_ask_do_not_duplicate_id():
    result = dialogue._new_result()
    response = {
        "id": "same-final-id",
        "status": "completed",
        "output": [{"type": "message", "content": [{"type": "output_text", "text": "done"}]}],
    }
    dialogue._checkpoint_response(result, response, None)

    class Client:
        def create_conversation(self, trace):
            return {"id": "conversation"}

        def ask_with_mcp(self, **kwargs):
            return response

    dialogue._ask(Client(), "work", None, result, {})
    assert result["response_ids"] == ["same-final-id"]


def test_completion_failure_never_marks_checkpoint_verified(monkeypatch):
    context = invocation()
    trace = create_invocation_trace(context)
    result = dialogue._new_result()
    result.update(status="completed", reply="provider answer")

    def fail(*args, **kwargs):
        raise OSError("private persistence failure details")

    monkeypatch.setattr(dialogue, "finish_invocation", fail)
    with pytest.raises(OSError):
        dialogue._finish(context, trace, result)
    assert result["checkpoint"]["reply_verified"] is False
    assert result["persistence"]["stage"] == "completion"
    assert "private persistence" not in json.dumps(result)


def test_saved_response_echo_exports_safe_effective_parameters_without_receipt():
    result = dialogue._new_result()
    dialogue._provider_diagnostics(
        result,
        {
            "status": "completed",
            "model": "gpt://project/deepseek-v4.1-flash/latest",
            "max_output_tokens": 393216,
            "truncation": "disabled",
            "reasoning": {"effort": "high", "private_text": "never-export"},
        },
    )
    assert result["provider_echo"] == {
        "model": "gpt://project/deepseek-v4.1-flash/latest",
        "max_output_tokens": 393216,
        "truncation": "disabled",
        "reasoning": {"effort": "high"},
    }
    assert "never-export" not in json.dumps(result)


@pytest.mark.parametrize("value", [float("nan"), object(), "\ud800"])
def test_unsupported_json_trace_never_replaces_prior_record(value):
    context = invocation()
    assert manager.persist_invocation_trace(context.invocation_id, {"prior": True})
    with pytest.raises(KristallError, match="unsupported JSON value"):
        manager.persist_invocation_trace(context.invocation_id, {"invalid": value})
    assert manager.get_invocation(context.invocation_id)["trace"] == {"prior": True}


@pytest.mark.parametrize("target", ["trace", "result"])
def test_deleted_invocation_during_chunk_staging_is_not_resurrected(monkeypatch, target):
    context = invocation()
    prepare = manager._prepare_value
    deleted = []

    def stage(invocation_id, field, value, **kwargs):
        prepared = prepare(invocation_id, field, value, **kwargs)
        if field == target and not deleted:
            with get_store().transaction() as tx:
                tx.delete(manager._key(context.invocation_id))
            deleted.append(True)
        return prepared

    monkeypatch.setattr(manager, "_prepare_value", stage)
    large = {"data": "x" * MAX_JOURNAL_FRAME_BYTES}
    if target == "trace":
        assert manager.persist_invocation_trace(context.invocation_id, large) is False
    else:
        assert manager.finish_invocation(context.invocation_id, large) is False
    assert manager.get_invocation(context.invocation_id) is None
    assert deleted == [True]


def test_terminal_status_wins_over_inflight_result_staging(monkeypatch):
    context = invocation()
    prepare = manager._prepare_value
    changed = []

    def stage(invocation_id, field, value, **kwargs):
        prepared = prepare(invocation_id, field, value, **kwargs)
        if field == "result" and not changed:
            with get_store().transaction() as tx:
                record = tx.get(manager._key(context.invocation_id))
                record["status"] = "cancelled"
                tx.set(manager._key(context.invocation_id), record)
            changed.append(True)
        return prepared

    monkeypatch.setattr(manager, "_prepare_value", stage)
    assert manager.finish_invocation(context.invocation_id, {"reply": "later"}) is False
    loaded = manager.get_invocation(context.invocation_id)
    assert loaded["status"] == "cancelled"
    assert loaded["result"] is None


@pytest.mark.parametrize("encoded", [b"{invalid-json", b"\xff"])
def test_validly_hashed_chunk_with_invalid_serialization_is_rejected(encoded):
    context = invocation()
    assert manager.persist_invocation_trace(
        context.invocation_id, {"data": "x" * MAX_JOURNAL_FRAME_BYTES}
    )
    record = raw_record(context)
    manifest = record["trace_chunks"]
    name = chunk_key(context, "trace", manifest)
    manifest.update(
        bytes=len(encoded), chunks=1, sha256=manager.hashlib.sha256(encoded).hexdigest()
    )
    with get_store().transaction() as tx:
        tx.set(name, {"data": base64.b64encode(encoded).decode()})
        tx.set(manager._key(context.invocation_id), record)
    with pytest.raises(KristallError, match="invalid invocation value encoding"):
        manager.get_invocation(context.invocation_id)


@pytest.mark.parametrize("target", ["trace", "result"])
@pytest.mark.parametrize("reason", ["recovery required", "journal frame exceeds size limit"])
def test_native_write_error_propagates_without_losing_prior_state(monkeypatch, target, reason):
    context = invocation()
    assert manager.persist_invocation_trace(context.invocation_id, {"prior": True})
    engine = get_store()._engine
    encode = engine._encode_frame
    attempts = []

    def reject(changes):
        if any(change["key"] == manager._key(context.invocation_id) for change in changes):
            attempts.append(True)
            raise KristallError(reason)
        return encode(changes)

    monkeypatch.setattr(engine, "_encode_frame", reject)
    with pytest.raises(KristallError, match=reason):
        if target == "trace":
            manager.persist_invocation_trace(context.invocation_id, {"next": True})
        else:
            manager.finish_invocation(context.invocation_id, {"reply": "new"})
    assert len(attempts) == (2 if reason in manager._VALUE_LIMIT_ERRORS else 1)
    loaded = manager.get_invocation(context.invocation_id)
    assert loaded["trace"] == {"prior": True}
    assert loaded["status"] == "running"
    assert loaded["result"] is None


@pytest.mark.parametrize("last_field", ["trace", "result"])
def test_native_aggregate_frame_overflow_retries_only_as_chunks(last_field):
    # Each JSON value is below the inline threshold. Three escaped values in one
    # invocation exceed a journal frame while remaining below its text budget.
    text = '"' * 250000
    value = {"data": text}
    assert len(json.dumps(value, separators=(",", ":")).encode()) < manager._INLINE_VALUE_BYTES
    context = manager.create_invocation(
        "aggregate-session", "aggregate-conversation", metadata={"description": text}
    )
    assert manager.start_invocation(context.invocation_id)
    if last_field == "trace":
        assert manager.finish_invocation(context.invocation_id, value)
        record = raw_record(context)
        record["trace"] = value
    else:
        assert manager.persist_invocation_trace(context.invocation_id, value)
        record = raw_record(context)
        record["result"] = value
    with (
        pytest.raises(KristallError, match="journal frame exceeds size limit"),
        get_store().transaction() as tx,
    ):
        tx.set(manager._key(context.invocation_id), record)
    if last_field == "trace":
        assert manager.persist_invocation_trace(context.invocation_id, value)
    else:
        assert manager.finish_invocation(context.invocation_id, value)
    loaded = manager.get_invocation(context.invocation_id)
    assert loaded["trace"] == value
    assert loaded["result"] == value
    assert last_field + "_chunks" in raw_record(context)


def test_small_serialization_with_native_node_overflow_uses_verified_chunks():
    context = invocation()
    value = {"items": [0] * 100001}
    assert len(json.dumps(value).encode()) < manager._INLINE_VALUE_BYTES
    with (
        pytest.raises(KristallError, match="value structure exceeds limit"),
        get_store().transaction() as tx,
    ):
        record = tx.get(manager._key(context.invocation_id))
        record["trace"] = value
        tx.set(manager._key(context.invocation_id), record)
    assert manager.persist_invocation_trace(context.invocation_id, value)
    assert manager.get_invocation(context.invocation_id)["trace"] == value
