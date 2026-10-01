import json
import subprocess

from scripts import claude_dispatch_guard as guard


HEAD = "a" * 40
OTHER_HEAD = "b" * 40


def marker(head=HEAD, attempt=None):
    suffix = f":{attempt}" if attempt else ""
    return f"<!-- agent-dispatch:maintainer:{head}{suffix} -->"


def comment(comment_id, body, association="OWNER"):
    return {"id": comment_id, "body": body, "author_association": association}


def completed(payload):
    return subprocess.CompletedProcess(
        args=["gh", "api"],
        returncode=0,
        stdout=json.dumps(payload),
        stderr="",
    )


def test_parse_marker_and_retry_suffix():
    first = guard.parse_dispatch_marker(marker())
    retry = guard.parse_dispatch_marker(marker(attempt="retry-2"))

    assert first == guard.DispatchMarker(HEAD, None)
    assert first.key == f"maintainer:{HEAD}"
    assert retry == guard.DispatchMarker(HEAD, "retry-2")
    assert retry.key == f"maintainer:{HEAD}:retry-2"
    assert guard.parse_dispatch_marker("plain text") is None


def test_invalid_or_repeated_marker_fails_closed():
    invalid = "<!-- agent-dispatch:maintainer:not-a-sha -->"
    repeated = marker() + "\n" + marker()

    assert guard.decide_dispatch(
        current_comment_id=1,
        current_author_association="OWNER",
        body=invalid,
        pr_head_sha=HEAD,
        comments=[comment(1, invalid)],
    ) == guard.DispatchDecision(False, "invalid_dispatch_marker")

    assert guard.decide_dispatch(
        current_comment_id=1,
        current_author_association="OWNER",
        body=repeated,
        pr_head_sha=HEAD,
        comments=[comment(1, repeated)],
    ) == guard.DispatchDecision(False, "invalid_dispatch_marker")


def test_unkeyed_dispatch_preserves_existing_tasks():
    decision = guard.decide_dispatch(
        current_comment_id=7,
        current_author_association="OWNER",
        body="normal repository task",
        pr_head_sha=HEAD,
        comments=[],
    )

    assert decision == guard.DispatchDecision(True, "unkeyed_dispatch")


def test_only_earliest_trusted_matching_comment_runs():
    body = marker()
    comments = [
        comment(9, body, "NONE"),
        comment(11, body, "OWNER"),
        comment(12, body, "COLLABORATOR"),
    ]

    primary = guard.decide_dispatch(
        current_comment_id=11,
        current_author_association="OWNER",
        body=body,
        pr_head_sha=HEAD,
        comments=comments,
    )
    duplicate = guard.decide_dispatch(
        current_comment_id=12,
        current_author_association="COLLABORATOR",
        body=body,
        pr_head_sha=HEAD,
        comments=comments,
    )

    assert primary == guard.DispatchDecision(True, "primary_dispatch", f"maintainer:{HEAD}")
    assert duplicate == guard.DispatchDecision(
        False,
        "duplicate_of_comment_11",
        f"maintainer:{HEAD}",
    )


def test_untrusted_or_stale_keyed_dispatch_is_rejected():
    body = marker()

    untrusted = guard.decide_dispatch(
        current_comment_id=1,
        current_author_association="NONE",
        body=body,
        pr_head_sha=HEAD,
        comments=[comment(1, body, "NONE")],
    )
    stale = guard.decide_dispatch(
        current_comment_id=2,
        current_author_association="OWNER",
        body=body,
        pr_head_sha=OTHER_HEAD,
        comments=[comment(2, body)],
    )

    assert untrusted.reason == "untrusted_dispatch_author"
    assert not untrusted.should_run
    assert stale.reason == "stale_dispatch_head"
    assert not stale.should_run


def test_missing_matching_key_fails_closed_and_malformed_other_comment_is_ignored():
    body = marker()
    malformed = "<!-- agent-dispatch:maintainer:broken -->"
    decision = guard.decide_dispatch(
        current_comment_id=5,
        current_author_association="OWNER",
        body=body,
        pr_head_sha=HEAD,
        comments=[comment(4, malformed), comment(6, marker(OTHER_HEAD))],
    )

    assert decision == guard.DispatchDecision(
        False,
        "dispatch_key_not_found",
        f"maintainer:{HEAD}",
    )


def test_retry_suffix_is_a_distinct_dispatch_key():
    initial = marker()
    retry = marker(attempt="retry-2")
    comments = [comment(1, initial), comment(2, retry)]

    decision = guard.decide_dispatch(
        current_comment_id=2,
        current_author_association="OWNER",
        body=retry,
        pr_head_sha=HEAD,
        comments=comments,
    )

    assert decision.should_run
    assert decision.dispatch_key == f"maintainer:{HEAD}:retry-2"


def test_fetch_pr_head_and_comment_pagination():
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        endpoint = command[-1]
        if endpoint == "repos/o/r/pulls/7":
            return completed({"head": {"sha": HEAD}})
        if "page=1" in endpoint:
            return completed([comment(n, "x") for n in range(1, 101)])
        if "page=2" in endpoint:
            return completed([comment(101, "x")])
        raise AssertionError(endpoint)

    assert guard.fetch_pr_head("o/r", 7, runner) == HEAD
    comments = guard.fetch_issue_comments("o/r", 7, runner)
    assert len(comments) == 101
    assert any("pulls/7" in call[-1] for call in calls)


def test_fetch_comments_rejects_non_list_and_pagination_overflow():
    def non_list(command, **kwargs):
        return completed({"bad": True})

    try:
        guard.fetch_issue_comments("o/r", 1, non_list)
    except ValueError as exc:
        assert "must be a list" in str(exc)
    else:
        raise AssertionError("expected non-list comments response to fail")

    def full_pages(command, **kwargs):
        return completed([comment(n, "x") for n in range(100)])

    try:
        guard.fetch_issue_comments("o/r", 1, full_pages, max_pages=2)
    except RuntimeError as exc:
        assert "pagination limit" in str(exc)
    else:
        raise AssertionError("expected pagination overflow")


def test_write_outputs(tmp_path):
    output = tmp_path / "github-output"
    guard.write_outputs(
        guard.DispatchDecision(False, "duplicate_of_comment_1", f"maintainer:{HEAD}"),
        str(output),
    )

    assert output.read_text(encoding="utf-8") == (
        "should_run=false\n"
        "reason=duplicate_of_comment_1\n"
        f"dispatch_key=maintainer:{HEAD}\n"
    )


def test_main_unkeyed_does_not_call_github(monkeypatch, tmp_path, capsys):
    output = tmp_path / "out"
    monkeypatch.setenv("DISPATCH_BODY", "ordinary task")
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setattr(
        guard,
        "fetch_pr_head",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected API call")),
    )

    assert guard.main() == 0
    assert "should_run=true" in output.read_text(encoding="utf-8")
    assert '"reason": "unkeyed_dispatch"' in capsys.readouterr().out


def test_main_keyed_uses_current_pr_and_comments(monkeypatch, tmp_path, capsys):
    output = tmp_path / "out"
    body = marker()
    monkeypatch.setenv("DISPATCH_BODY", body)
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv("DISPATCH_REPO", "o/r")
    monkeypatch.setenv("DISPATCH_ISSUE_NUMBER", "7")
    monkeypatch.setenv("DISPATCH_COMMENT_ID", "11")
    monkeypatch.setenv("DISPATCH_AUTHOR_ASSOCIATION", "OWNER")
    monkeypatch.setattr(guard, "fetch_pr_head", lambda repo, number: HEAD)
    monkeypatch.setattr(
        guard,
        "fetch_issue_comments",
        lambda repo, number: [comment(11, body)],
    )

    assert guard.main() == 0
    assert "should_run=true" in output.read_text(encoding="utf-8")
    assert '"reason": "primary_dispatch"' in capsys.readouterr().out


def test_main_guard_error_fails_closed(monkeypatch, tmp_path, capsys):
    output = tmp_path / "out"
    monkeypatch.setenv("DISPATCH_BODY", marker())
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.delenv("DISPATCH_REPO", raising=False)

    assert guard.main() == 0
    assert "should_run=false" in output.read_text(encoding="utf-8")
    assert "guard_error_KeyError" in capsys.readouterr().out
