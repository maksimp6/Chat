"""Adverse public checkpoint/ingress boundaries and live authorization changes."""

import copy
import hashlib
import json

import pytest

from agent_office.claude_checkpoint import (
    open_checkpoint,
    pack_checkpoint,
    seal_checkpoint,
    unpack_checkpoint,
)
from tests.test_claude_checkpoint import BINDING, PRIVATE_CONTENT, SECRET as CRYPTO_SECRET
from tests.test_claude_ci_dialogue import SECRET, transport
from tests.test_claude_session_ingress import _anchor, _comment


@pytest.mark.parametrize("value", [BINDING["native_session_id"].upper(), 17])
def test_noncanonical_native_identity_is_not_a_second_checkpoint_scope(value):
    scope = dict(BINDING, native_session_id=value)
    with pytest.raises(ValueError, match="^checkpoint_invalid$"):
        seal_checkpoint(PRIVATE_CONTENT, CRYPTO_SECRET, scope)


@pytest.mark.parametrize(
    ("state", "transcript"),
    [(None, b"history"), ({}, "not bytes"), ({"cost": float("nan")}, b"history")],
)
def test_only_json_safe_explicit_state_and_transcript_bytes_can_be_packed(state, transcript):
    with pytest.raises(ValueError, match="^checkpoint_invalid$"):
        pack_checkpoint(state, BINDING["native_session_id"], transcript)


def test_transcript_envelope_cannot_exceed_admitted_private_payload_size():
    with pytest.raises(ValueError, match="^checkpoint_invalid$"):
        pack_checkpoint({}, BINDING["native_session_id"], b"x" * (16 * 1024 * 1024))


@pytest.mark.parametrize("payload", ["not bytes", b"x" * (16 * 1024 * 1024 + 1)])
def test_unpack_rejects_unbounded_or_nonbyte_payload_before_restoration(payload):
    with pytest.raises(ValueError, match="^checkpoint_invalid$"):
        unpack_checkpoint(payload)


@pytest.mark.parametrize("mutation", ["schema", "state", "base64"])
def test_invalid_explicit_envelope_fields_cannot_be_restored(mutation):
    envelope = json.loads(pack_checkpoint({}, BINDING["native_session_id"], b"history"))
    envelope[{"schema": "schema", "state": "state", "base64": "transcript_b64"}[mutation]] = {
        "schema": True,
        "state": [],
        "base64": "not!base64",
    }[mutation]
    with pytest.raises(ValueError, match="^checkpoint_invalid$"):
        unpack_checkpoint(json.dumps(envelope).encode())


@pytest.mark.parametrize(
    "comment",
    [
        None,
        {"id": True},
        {"id": 0},
        dict(_comment(1), body=[]),
        dict(_comment(1), user={"type": "User", "login": 1}),
    ],
)
def test_malformed_author_or_source_identity_cannot_be_admitted(comment):
    from agent_office.claude_ci import select_commands

    with pytest.raises(ValueError, match="^comment_invalid$"):
        select_commands([comment], 0)


@pytest.mark.parametrize(
    "mutation", ["schema", "kind", "session", "watermark", "artifact", "name", "digest"]
)
def test_malformed_trusted_anchor_never_becomes_a_restore_candidate(mutation):
    from agent_office.claude_ci import choose_checkpoint

    marker = _anchor("committed", 2, 1, 8)
    changes = {
        "schema": ("schema", True),
        "kind": ("kind", "unknown"),
        "session": ("session_id", "not-a-UUID"),
        "watermark": ("watermark", -1),
        "artifact": ("artifact_id", True),
        "name": ("artifact_name", "../../checkpoint"),
        "digest": ("ciphertext_sha256", "not-a-digest"),
    }
    field, value = changes[mutation]
    marker[field] = value
    with pytest.raises(ValueError, match="^checkpoint_anchor_invalid$"):
        choose_checkpoint([marker])


@pytest.mark.parametrize(
    "markers", [None, [None], [_anchor("started", 1, 1, 1), _anchor("committed", 2, 2, 2)]]
)
def test_checkpoint_anchor_container_and_generation_pair_must_be_valid(markers):
    from agent_office.claude_ci import choose_checkpoint

    with pytest.raises(ValueError, match="^checkpoint_anchor_invalid$"):
        choose_checkpoint(markers)


def test_cheap_eligibility_requires_an_authorized_explicit_start_or_existing_anchor(transport):
    ci, github, spy, wake, commit = transport
    github.add_comment("Ordinary issue discussion")
    assert ci.eligible_dialogue(github, 703) is False
    github.add_comment("@claude-lite session Start persistent dialogue")
    github.authorized = False
    assert ci.eligible_dialogue(github, 703) is False
    github.authorized = True
    assert ci.eligible_dialogue(github, 703) is True
    commit(wake(), 201)
    assert ci.eligible_dialogue(github, 703) is True
    assert len(spy.calls) == 1


def test_cheap_eligibility_does_not_treat_missing_checkpoint_label_as_fresh_start(transport):
    ci, github, spy, *_ = transport
    github.labels.append("claude-session")
    with pytest.raises(ValueError, match="^checkpoint_missing$"):
        ci.eligible_dialogue(github, 703)
    assert spy.calls == []


@pytest.mark.parametrize(
    "body", ["<!-- claude-session-checkpoint:broken", "<!-- claude-session-checkpoint:not JSON -->"]
)
def test_malformed_latest_trusted_marker_blocks_even_without_new_command(transport, body):
    ci, github, spy, *_ = transport
    github.add_comment(body, user_type="Bot", association="MEMBER")
    with pytest.raises(ValueError, match="^checkpoint_anchor_invalid$"):
        ci.eligible_dialogue(github, 703)
    assert spy.calls == []


def _replace_record(github, metadata, mutation):
    record = unpack_checkpoint(open_checkpoint(github.files[201], SECRET, metadata["binding"]))
    mutation(record["state"])
    ciphertext = seal_checkpoint(pack_checkpoint(**record), SECRET, metadata["binding"])
    github.files[201] = ciphertext
    github.artifacts[201]["size_in_bytes"] = len(ciphertext)
    marker = copy.deepcopy(github.markers[-1])
    marker["ciphertext_sha256"] = hashlib.sha256(ciphertext).hexdigest()
    github.comments[-1]["body"] = github.encode_marker(marker)


def test_authenticated_state_cannot_move_the_source_completion_cursor(transport):
    _, github, spy, wake, commit = transport
    github.add_comment("@claude-lite session One completed source event")
    metadata = wake()
    commit(metadata, 201)
    _replace_record(
        github, metadata, lambda state: state.update(completed_event_ids=["issue-comment:999"])
    )
    github.add_comment("Continue")
    with pytest.raises(ValueError, match="^checkpoint_invalid$"):
        wake(101)
    assert len(spy.calls) == 1


def test_closed_private_session_cannot_be_woken_by_comment(transport):
    _, github, spy, wake, commit = transport
    github.add_comment("@claude-lite session A session that was explicitly ended")
    metadata = wake()
    commit(metadata, 201)
    _replace_record(
        github, metadata, lambda state: state.update(status="closed", reason="session_closed")
    )
    github.add_comment("Continue")
    with pytest.raises(ValueError, match="^checkpoint_session_blocked$"):
        wake(101)
    assert len(spy.calls) == 1


def test_queued_command_authority_is_rechecked_before_next_paid_batch(transport):
    _, github, spy, wake, commit = transport
    for index in range(11):
        github.add_comment(f"@claude-lite session Source instruction {index}")
    commit(wake(), 201)
    github.comments[8]["author_association"] = "NONE"
    with pytest.raises(ValueError, match="^checkpoint_queue_unauthorized$"):
        wake(101)
    assert len(spy.calls) == 8


def test_blocked_provider_turn_has_started_marker_but_no_upload_commit(transport):
    _, github, spy, wake, _ = transport
    github.add_comment("@claude-lite session Uncertain native outcome")
    spy.fail_once = True
    with pytest.raises(ValueError, match="^checkpoint_provider_blocked$"):
        wake()
    assert len(spy.calls) == 1
    assert github.markers[-1]["kind"] == "started"
    assert github.answers == []


def test_anchor_for_another_issue_is_not_used_before_any_native_call(transport):
    ci, github, spy, wake, _ = transport
    marker = _anchor("committed", 1, 1, 1)
    marker["issue_number"] = 716
    github.add_comment(ci.encode_marker(marker), user_type="Bot", association="MEMBER")
    github.add_comment("@claude-lite session Use this issue")
    with pytest.raises(ValueError, match="^checkpoint_anchor_invalid$"):
        wake()
    assert spy.calls == []
