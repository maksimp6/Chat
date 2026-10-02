import io
import json

import pytest

import agent_office.maintainer_dispatch_guard as guard
from agent_office.maintainer_dispatch_guard import (
    GitHub,
    GuardDecision,
    build_idempotency_key,
    claim_comment_body,
    duplicate_comment_body,
    evaluate_maintainer_dispatch,
    run_guard,
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
            "<!-- alice-maintainer-dispatch-claim:"
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


def test_maintainer_dispatch_requires_exact_head_sha():
    with pytest.raises(ValueError, match="pull request head SHA is required"):
        evaluate_maintainer_dispatch(
            event("@claude-lite Act as Maintainer for this exact head."),
            {"head": {}},
            [],
            repo=REPO,
            run_id=127,
        )


def test_replayed_source_comment_is_duplicate():
    key = build_idempotency_key(REPO, 696, HEAD)
    decision = evaluate_maintainer_dispatch(
        event("@claude-lite Act as Maintainer for this exact head.", comment_id=200),
        pr(),
        [claim_comment(key=key, source_comment_id=200, run_id=123)],
        repo=REPO,
        run_id=124,
    )

    assert decision.run_model is False
    assert decision.reason == "duplicate"
    assert decision.prior_run_id == 123


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


def test_machine_readable_comments_do_not_reproduce_executable_trigger():
    first = GuardDecision(
        True,
        "claimed",
        build_idempotency_key(REPO, 696, HEAD),
        696,
        HEAD,
        200,
    )
    duplicate = GuardDecision(
        False,
        "duplicate",
        first.idempotency_key,
        696,
        HEAD,
        201,
        123,
    )

    claim = claim_comment_body(first, 123)
    dup = duplicate_comment_body(duplicate, 124)

    assert "alice-maintainer-dispatch-claim:" in claim
    assert '"run_id":123' in claim
    assert "alice-maintainer-dispatch-duplicate:" in dup
    assert '"prior_run_id":123' in dup
    assert "@claude-lite" not in claim + dup


def test_claim_parser_ignores_untrusted_malformed_and_non_dict_payloads():
    key = build_idempotency_key(REPO, 696, HEAD)
    untrusted = claim_comment(key=key)
    untrusted["user"] = {"login": "someone"}
    malformed = {
        "body": "<!-- alice-maintainer-dispatch-claim:{broken} -->",
        "user": {"login": "github-actions[bot]"},
    }
    non_dict = {
        "body": "<!-- alice-maintainer-dispatch-claim:[1,2] -->",
        "user": {"login": "github-actions[bot]"},
    }
    plain = {"body": "nothing here", "user": {"login": "github-actions[bot]"}}

    assert guard._marker_payload(untrusted, guard.CLAIM_RE) is None
    assert guard._marker_payload(malformed, guard.CLAIM_RE) is None
    assert guard._marker_payload(non_dict, guard.CLAIM_RE) is None
    assert guard._marker_payload(plain, guard.CLAIM_RE) is None


class FakeResponse(io.BytesIO):
    def __init__(self, payload, headers=None):
        super().__init__(json.dumps(payload).encode())
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeOpener:
    def __init__(self):
        self.calls = []

    def __call__(self, request, timeout):
        self.calls.append(
            (request.get_method(), request.full_url, request.data, dict(request.headers))
        )
        if request.full_url.endswith("/pulls/696"):
            return FakeResponse({"head": {"sha": HEAD}})
        if request.full_url.endswith("/comments?page=2"):
            return FakeResponse([{"id": 2}])
        if request.full_url.endswith("/comments"):
            return FakeResponse(
                [{"id": 1}],
                {
                    "Link": '<https://api.github.com/repos/maksimp6/Chat/issues/696/comments?page=2>; rel="next"'
                },
            )
        if request.get_method() == "POST":
            return FakeResponse({"id": 3})
        raise AssertionError(request.full_url)


def test_github_client_paginates_and_posts_with_token():
    opener = FakeOpener()
    gh = GitHub("secret", REPO, opener=opener)

    assert gh.get(gh.repo_path("/pulls/696")) == {"head": {"sha": HEAD}}
    assert gh.paginate(gh.repo_path("/issues/696/comments")) == [{"id": 1}, {"id": 2}]
    assert gh.post(gh.repo_path("/issues/696/comments/new"), {"body": "x"}) == {"id": 3}
    assert opener.calls[0][3]["Authorization"] == "Bearer secret"
    assert json.loads(opener.calls[-1][2]) == {"body": "x"}
    assert guard._next_link("") is None


class FakeGuardGitHub:
    def __init__(self, comments=None):
        self.repo = REPO
        self.comments = comments or []
        self.posts = []

    def repo_path(self, suffix):
        return f"/repos/{self.repo}{suffix}"

    def get(self, path):
        assert path == f"/repos/{REPO}/pulls/696"
        return pr()

    def paginate(self, path):
        assert path == f"/repos/{REPO}/issues/696/comments?per_page=100"
        return list(self.comments)

    def post(self, path, payload):
        assert path == f"/repos/{REPO}/issues/696/comments"
        self.posts.append(payload)
        return {"id": 999}


def test_run_guard_posts_claim_before_first_model_execution():
    gh = FakeGuardGitHub()
    decision = run_guard(
        gh,
        event("@claude-lite Act as Maintainer for this exact head."),
        run_id=123,
    )

    assert decision.run_model is True
    assert decision.reason == "claimed"
    assert len(gh.posts) == 1
    assert "alice-maintainer-dispatch-claim:" in gh.posts[0]["body"]


def test_run_guard_posts_duplicate_evidence_only_once_per_run():
    key = build_idempotency_key(REPO, 696, HEAD)
    prior = claim_comment(key=key, source_comment_id=200, run_id=123)
    gh = FakeGuardGitHub([prior])
    current = event(
        "@claude-lite Act as Maintainer for this exact head.",
        comment_id=201,
    )

    first = run_guard(gh, current, run_id=124)
    assert first.run_model is False
    assert first.reason == "duplicate"
    assert "alice-maintainer-dispatch-duplicate:" in gh.posts[0]["body"]

    duplicate_marker = {
        "body": gh.posts[0]["body"],
        "user": {"login": "github-actions[bot]"},
    }
    gh.comments.append(duplicate_marker)
    gh.posts.clear()

    second = run_guard(gh, current, run_id=124)
    assert second.run_model is False
    assert gh.posts == []


def test_run_guard_bypasses_non_maintainer_without_github_reads():
    class NoIoGitHub:
        repo = REPO

        def get(self, path):
            raise AssertionError("unexpected get")

        def paginate(self, path):
            raise AssertionError("unexpected paginate")

    decision = run_guard(
        NoIoGitHub(),
        event("@claude-lite fix the failing test."),
        run_id=125,
    )
    assert decision.reason == "not_maintainer"
    assert decision.run_model is True


def test_main_writes_outputs_and_summary(monkeypatch, tmp_path):
    event_path = tmp_path / "event.json"
    output_path = tmp_path / "output.txt"
    summary_path = tmp_path / "summary.md"
    event_path.write_text(json.dumps(event("@claude-lite Act as Maintainer.")), encoding="utf-8")

    decision = GuardDecision(
        False,
        "duplicate",
        "key",
        696,
        HEAD,
        201,
        123,
    )
    monkeypatch.setattr(guard, "run_guard", lambda gh, payload, run_id: decision)
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    monkeypatch.setenv("GITHUB_REPOSITORY", REPO)
    monkeypatch.setenv("GITHUB_RUN_ID", "124")
    monkeypatch.setenv("GITHUB_TOKEN", "secret")
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_path))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_path))

    assert guard.main() == 0
    output = output_path.read_text(encoding="utf-8")
    summary = summary_path.read_text(encoding="utf-8")
    assert "run_model=false" in output
    assert "reason=duplicate" in output
    assert "idempotency_key=key" in output
    assert f"head_sha={HEAD}" in output
    assert '"reason": "duplicate"' in summary


def test_output_helpers_are_noops_without_github_paths(monkeypatch):
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)

    guard._write_output("x", "y")
    guard._write_summary(GuardDecision(True, "not_maintainer"))


def test_main_requires_github_environment(monkeypatch):
    monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    monkeypatch.delenv("GITHUB_RUN_ID", raising=False)

    with pytest.raises(SystemExit, match="GITHUB_EVENT_PATH"):
        guard.main()
