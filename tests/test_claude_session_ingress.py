"""Issue #703 opt-in comment ingress; association is only the first auth gate.

The workflow must also check current repository write permission before any paid
dispatch. This pure parser neither shells out nor treats ordinary comments as
commands; all content/identities here are synthetic.
"""

import copy

import pytest


def _comment(comment_id, body="@claude-lite session Explain persistence", *, association="OWNER", user_type="User", login="owner"):
    return {
        "id": comment_id,
        "body": body,
        "author_association": association,
        "user": {"login": login, "type": user_type},
    }


@pytest.fixture
def select():
    from agent_office.claude_ci import select_commands

    return select_commands


def test_explicit_commands_are_ordered_and_use_stable_source_ids(select):
    comments = [_comment(9, "@claude-lite session Third"), _comment(2, "@claude-lite session First"), _comment(5, "@claude-lite session Second")]
    assert select(comments, after_id=0) == [
        {"event_id": "issue-comment:2", "comment_id": 2, "message": "First", "author": "owner"},
        {"event_id": "issue-comment:5", "comment_id": 5, "message": "Second", "author": "owner"},
        {"event_id": "issue-comment:9", "comment_id": 9, "message": "Third", "author": "owner"},
    ]


def test_cursor_excludes_already_acknowledged_comments(select):
    assert select([_comment(2), _comment(5), _comment(9)], after_id=5) == [
        {"event_id": "issue-comment:9", "comment_id": 9, "message": "Explain persistence", "author": "owner"}
    ]


def test_received_duplicate_is_delivered_exactly_once_not_dropped(select):
    first = _comment(4)
    assert len(select([first, copy.deepcopy(first)], after_id=0)) == 1


def test_conflicting_same_source_id_fails_closed(select):
    with pytest.raises(ValueError, match="^comment_invalid$"):
        select([_comment(4, "@claude-lite session One"), _comment(4, "@claude-lite session Different")], after_id=0)


@pytest.mark.parametrize("association", ["OWNER", "MEMBER", "COLLABORATOR"])
def test_allowed_associations_are_only_candidates_for_write_permission_gate(select, association):
    assert len(select([_comment(1, association=association)], after_id=0)) == 1


@pytest.mark.parametrize("association", ["NONE", "FIRST_TIMER", "CONTRIBUTOR", "FIRST_TIME_CONTRIBUTOR"])
def test_nonwriter_associations_never_become_commands(select, association):
    assert select([_comment(1, association=association)], after_id=0) == []


def test_bot_cannot_wake_its_own_session_even_with_owner_association(select):
    assert select([_comment(1, user_type="Bot", login="github-actions[bot]")], after_id=0) == []


@pytest.mark.parametrize(
    "body",
    [
        "@claude-lite Ordinary existing task",
        "Status says @claude-lite session Explain persistence",
        "> @claude-lite session Quoted command",
        "`@claude-lite session` is the trigger",
        "@claude-lite sessionize This is a different word",
        "@claude-lite session",
        "@claude-lite session   \n",
        "<!-- claude-session-checkpoint: @claude-lite session Not a command -->",
    ],
)
def test_only_leading_exact_opt_in_with_nonempty_command_is_selected(select, body):
    assert select([_comment(1, body)], after_id=0) == []


def test_command_is_preserved_as_data_without_shell_interpolation(select, tmp_path):
    marker = tmp_path / "must-not-exist"
    command = f"Discuss $(touch {marker}) and `touch {marker}` literally"
    result = select([_comment(1, f" \n @claude-lite session\n{command}")], after_id=0)
    assert result[0]["message"] == command
    assert not marker.exists()


@pytest.mark.parametrize("cursor", [-1, True, "1", None])
def test_invalid_cursor_is_generic_rejection(select, cursor):
    with pytest.raises(ValueError, match="^comment_invalid$"):
        select([_comment(3)], after_id=cursor)


def test_parser_does_not_mutate_ingress_or_author_evidence(select):
    comments = [_comment(2), _comment(1)]
    before = copy.deepcopy(comments)
    select(comments, after_id=0)
    assert comments == before


def test_routed_session_accepts_plain_followup_in_same_queue(select):
    comments = [_comment(1, "@claude-lite session Start dialogue"), _comment(2, "Explain your previous answer")]
    assert select(comments, after_id=1, routed=True) == [
        {"event_id": "issue-comment:2", "comment_id": 2, "message": "Explain your previous answer", "author": "owner"}
    ]
    assert select(comments, after_id=1, routed=False) == []


def test_routed_session_still_strips_explicit_opt_in_prefix(select):
    assert select([_comment(2, "@claude-lite session Next turn")], after_id=1, routed=True)[0]["message"] == "Next turn"


def test_routing_hint_never_authorizes_outsiders_bots_or_checkpoint_metadata(select):
    comments = [
        _comment(2, "Run my command", association="NONE"),
        _comment(3, "Wake myself", user_type="Bot"),
        _comment(4, "<!-- claude-session-checkpoint: hidden metadata -->"),
        _comment(5, " \n "),
    ]
    assert select(comments, after_id=0, routed=True) == []


def _anchor(kind, comment_id, generation, watermark):
    marker = {
        "schema": 1,
        "kind": kind,
        "comment_id": comment_id,
        "repo_id": 12345,
        "issue_number": 703,
        "session_id": "62f26d78-5ad7-4e21-a682-f486649c40ba",
        "generation": generation,
        "run_id": 100 + generation,
        "watermark": watermark,
    }
    if kind == "committed":
        marker.update(
            native_session_id="dc462c2c-bf29-4cce-8b18-6ba331061f09",
            artifact_id=200 + generation,
            artifact_name=f"claude-session-checkpoint-{generation}",
            head_sha="a" * 40,
            ciphertext_sha256="b" * 64,
        )
    return marker


@pytest.fixture
def choose():
    from agent_office.claude_ci import choose_checkpoint

    return choose_checkpoint


def test_latest_committed_generation_is_selected_without_older_fallback(choose):
    older = _anchor("committed", 2, 1, 8)
    newer = _anchor("committed", 4, 2, 15)
    markers = [newer, _anchor("started", 1, 1, 8), older, _anchor("started", 3, 2, 15)]
    before = copy.deepcopy(markers)
    assert choose(markers) == newer
    assert markers == before
    assert choose([]) is None


@pytest.mark.parametrize("generation", [1, 2])
def test_started_after_committed_generation_blocks_ambiguous_replay(choose, generation):
    markers = [_anchor("committed", 2, 1, 8), _anchor("started", 3, generation, 9)]
    with pytest.raises(ValueError, match="^checkpoint_incomplete$"):
        choose(markers)


def test_first_uncommitted_start_has_no_safe_fresh_session_fallback(choose):
    with pytest.raises(ValueError, match="^checkpoint_incomplete$"):
        choose([_anchor("started", 1, 1, 2)])


def test_committed_watermark_cannot_regress(choose):
    with pytest.raises(ValueError, match="^checkpoint_anchor_invalid$"):
        choose([_anchor("committed", 1, 1, 15), _anchor("committed", 2, 2, 8)])


def test_malformed_newest_committed_anchor_is_not_replaced_with_old_snapshot(choose):
    latest = _anchor("committed", 2, 2, 15)
    latest.pop("artifact_id")
    with pytest.raises(ValueError, match="^checkpoint_anchor_invalid$"):
        choose([_anchor("committed", 1, 1, 8), latest])


@pytest.mark.parametrize(("field", "value"), [("repo_id", 999), ("issue_number", 716), ("session_id", "379eeb46-ef66-4fe3-b36d-aa10363e8552")])
def test_anchors_cannot_mix_repository_issue_or_logical_session(choose, field, value):
    latest = _anchor("committed", 2, 2, 15)
    latest[field] = value
    with pytest.raises(ValueError, match="^checkpoint_anchor_invalid$"):
        choose([_anchor("committed", 1, 1, 8), latest])
