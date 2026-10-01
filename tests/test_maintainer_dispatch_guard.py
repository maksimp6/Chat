from datetime import UTC, datetime

from agent_office.maintainer_dispatch_guard import (
    build_idempotency_key,
    evaluate_maintainer_dispatch,
)


NOW = "2026-10-01T10:47:00Z"
REPO = "maksimp6/Chat"
HEAD = "b49236e42717ba59e7a0e6701154318fb323777a"


def event(body, *, comment_id=200, association="OWNER", number=696):
    return {
        "action": "created",
        "comment": {
            "id": comment_id,
            "body": body,
            "author_association": association,
        },
        "issue": {
            "number": number,
            "pull_request": {"url": f"https://api.github.com/repos/{REPO}/pulls/{number}"},
        },
    }


def pr(head=HEAD):
    return {"head": {"sha": head}}


def claim_comment(*, key, source_comment_id=100, run_id=111, head=HEAD):
    return {
        "id": 900,
        "body": (
            '<!-- alice-maintainer-dispatch-claim:'
            '{"key":"%s","source_comment_id":%d,"run_id":%d,"head_sha":"%s","retry_of":null}'
            " -->"
        )
        % (key, source_comment_id, run_id, head),
        "user": {"login": "github-actions[bot]"},
        "created_at": NOW,
    }


def claude_outcome(run_id, body):
    return {
        "id": 901,
        "body": body + f"\n\n[View job run](https://github.com/{REPO}/actions/runs/{run_id})",
        "user": {"login": "claude[bot]"},
        "created_at": NOW,
    }


def test_first_maintainer_handoff_claims_exact_head():
    decision = evaluate_maintainer_dispatch(
        event("@claude-lite Act as Maintainer for this exact head."),
        pr(),
        [],
        repo=REPO,
        run_id=123,
    )

    assert decision.run_model is True
    assert decision.reason == "claimed"
    assert decision.idempotency_key == build_idempotency_key(REPO, 696, HEAD)
    assert decision.pr_number == 696
    assert decision.head_sha == HEAD


def test_duplicate_maintainer_handoff_same_head_is_suppressed():
    key = build_idempotency_key(REPO, 696, HEAD)
    decision = evaluate_maintainer_dispatch(
        event("@claude-lite Act as Maintainer for this exact head.", comment_id=201),
        pr(),
        [claim_comment(key=key, source_comment_id=200, run_id=123)],
        repo=REPO,
        run_id=124,
    )

    assert decision.run_model is False
    assert decision.reason == "duplicate"
    assert decision.prior_run_id == 123


def test_changed_head_allows_new_maintainer_execution():
    old_key = build_idempotency_key(REPO, 696, HEAD)
    new_head = "f" * 40
    decision = evaluate_maintainer_dispatch(
        event("@claude-lite Act as Maintainer for this exact head.", comment_id=202),
        pr(new_head),
        [claim_comment(key=old_key, source_comment_id=200, run_id=123)],
        repo=REPO,
        run_id=125,
    )

    assert decision.run_model is True
    assert decision.reason == "claimed"
    assert decision.head_sha == new_head


def test_non_maintainer_claude_lite_task_is_not_suppressed():
    key = build_idempotency_key(REPO, 696, HEAD)
    decision = evaluate_maintainer_dispatch(
        event("@claude-lite fix the failing observer test.", comment_id=203),
        pr(),
        [claim_comment(key=key, source_comment_id=200, run_id=123)],
        repo=REPO,
        run_id=126,
    )

    assert decision.run_model is True
    assert decision.reason == "not_maintainer"


def test_untrusted_maintainer_comment_is_rejected_before_model():
    decision = evaluate_maintainer_dispatch(
        event(
            "@claude-lite Act as Maintainer for this exact head.",
            association="NONE",
        ),
        pr(),
        [],
        repo=REPO,
        run_id=127,
    )

    assert decision.run_model is False
    assert decision.reason == "untrusted_author"


def test_explicit_retry_after_failed_attempt_is_allowed_once():
    key = build_idempotency_key(REPO, 696, HEAD)
    comments = [
        claim_comment(key=key, source_comment_id=200, run_id=123),
        claude_outcome(123, "Claude encountered an error after 0s"),
    ]
    decision = evaluate_maintainer_dispatch(
        event(
            "@claude-lite RETRY: Act as Maintainer after the failed attempt.",
            comment_id=204,
        ),
        pr(),
        comments,
        repo=REPO,
        run_id=128,
    )

    assert decision.run_model is True
    assert decision.reason == "retry_claimed"
    assert decision.retry_of == 123


def test_retry_without_failed_or_blocked_prior_attempt_is_suppressed():
    key = build_idempotency_key(REPO, 696, HEAD)
    comments = [
        claim_comment(key=key, source_comment_id=200, run_id=123),
        claude_outcome(123, "MERGE-READY"),
    ]
    decision = evaluate_maintainer_dispatch(
        event(
            "@claude-lite RETRY: Act as Maintainer again.",
            comment_id=205,
        ),
        pr(),
        comments,
        repo=REPO,
        run_id=129,
    )

    assert decision.run_model is False
    assert decision.reason == "retry_not_eligible"
