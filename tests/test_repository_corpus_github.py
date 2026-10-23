"""Deterministic controls for paginated GitHub evidence, limits and cache scope."""

from __future__ import annotations

import io
import urllib.request
import zipfile

import pytest

from scripts.repository_corpus_github import (
    CollectionError,
    GitHubReader,
    _DownloadRedirect,
    collect_github,
)


class Sink:
    repository = "owner/repo"

    def __init__(self, directory):
        self.cache_dir = directory
        self.records = []
        self.gaps = []

    def add(self, kind, source_id, url, version, raw, metadata=None):
        self.records.append(
            dict(
                kind=kind, source_id=source_id, url=url, version=version, raw=raw, metadata=metadata
            )
        )

    def gap(self, kind, source_id, reason, metadata=None):
        self.gaps.append(dict(kind=kind, source_id=source_id, reason=reason, metadata=metadata))


def archive():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as file:
        file.writestr("job/log.txt", "step result\n")
    return buffer.getvalue()


class Reader:
    max_download_bytes = 128 * 1024 * 1024
    stop_reason = None

    def __init__(self, responses=None):
        self.responses = responses or {}
        self.calls = []
        self.binary_calls = []
        self.graph_calls = []

    def json(self, path, *, immutable=False):
        self.calls.append((path, immutable))
        value = self.responses.get(path)
        if isinstance(value, Exception):
            raise value
        if value is not None:
            return value
        if path == "/repos/owner/repo":
            return {"id": 1, "full_name": "owner/repo"}
        if "/attempts/" in path and "?" not in path:
            return {"id": 10, "status": "completed"}
        if "/jobs?" in path:
            return {"total_count": 1, "jobs": [{"id": 100, "name": "build"}]}
        if "/actions/runs?" in path:
            return {"total_count": 0, "workflow_runs": []}
        if "/actions/artifacts?" in path:
            return {"total_count": 0, "artifacts": []}
        return []

    def binary(self, path, *, immutable=False):
        self.binary_calls.append((path, immutable))
        result = self.responses.get(path, archive())
        if isinstance(result, Exception):
            raise result
        return result

    def graphql(self, query, variables):
        self.graph_calls.append((query, variables))
        return {
            "data": {
                "repository": {
                    "discussions": {
                        "totalCount": 0,
                        "nodes": [],
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                    }
                }
            }
        }


def test_open_closed_issues_prs_comments_and_paginated_reviews(tmp_path):
    first = [
        {"id": number, "number": number, "state": "closed", "body": "historical"}
        for number in range(1, 101)
    ]
    reader = Reader(
        {
            "/repos/owner/repo/issues?state=all&sort=created&direction=asc&per_page=100&page=1": first,
            "/repos/owner/repo/issues?state=all&sort=created&direction=asc&per_page=100&page=2": [
                {
                    "id": 101,
                    "number": 101,
                    "state": "open",
                    "pull_request": {"url": "https://api.github.com/repos/owner/repo/pulls/101"},
                }
            ],
            "/repos/owner/repo/pulls?state=all&sort=created&direction=asc&per_page=100&page=1": [
                {"id": 101, "number": 101, "state": "closed", "head": {"sha": "a" * 40}}
            ],
            "/repos/owner/repo/issues/comments?sort=created&direction=asc&per_page=100&page=1": [
                {"id": 5, "body": "discussion remains", "user": {"login": "executor"}}
            ],
            "/repos/owner/repo/pulls/101/reviews?per_page=100&page=1": [
                {"id": 6, "body": "review", "state": "CHANGES_REQUESTED"}
            ],
        }
    )
    sink = Sink(tmp_path)
    result = collect_github("owner/repo", "", sink, reader=reader)
    assert result["counts"]["issue"] == 101
    assert result["counts"]["pull_request"] == 1
    assert result["counts"]["pull_request_review"] == 1
    assert b'"login":"executor"' in next(
        row["raw"] for row in sink.records if row["kind"] == "issue_comment"
    )
    assert all(
        "state=all" in path for path, _ in reader.calls if "/issues?" in path or "/pulls?" in path
    )
    assert result["generation_requests"] == 0


def test_missing_source_expiry_and_attempts_are_visible(tmp_path):
    reader = Reader(
        {
            "/repos/owner/repo/issues/comments?sort=created&direction=asc&per_page=100&page=1": CollectionError(
                "github_http_403"
            ),
            "/repos/owner/repo/actions/runs?per_page=100&page=1": {
                "total_count": 1,
                "workflow_runs": [{"id": 10, "run_attempt": 2, "status": "completed"}],
            },
            "/repos/owner/repo/actions/artifacts?per_page=100&page=1": {
                "total_count": 2,
                "artifacts": [{"id": 50, "expired": True}, {"id": 51, "expired": False}],
            },
        }
    )
    sink = Sink(tmp_path)
    result = collect_github("owner/repo", "", sink, reader=reader)
    assert result["status"] == "partial"
    assert result["counts"]["actions_logs"] == 2
    assert result["counts"]["actions_job"] == 2
    assert any(row["reason"] == "github_http_403" for row in sink.gaps)
    assert any(
        row["reason"] == "artifact_expired" and "/50/zip" in row["source_id"] for row in sink.gaps
    )
    assert all("/50/zip" not in path for path, _ in reader.binary_calls)
    assert ("/repos/owner/repo/actions/runs/10/attempts/1/logs", True) in reader.binary_calls
    assert ("/repos/owner/repo/actions/runs/10/attempts/2/logs", True) in reader.binary_calls


def test_count_mismatch_and_page_limit_are_not_complete(tmp_path):
    reader = Reader(
        {
            "/repos/owner/repo/actions/runs?per_page=100&page=1": {
                "total_count": 40000,
                "workflow_runs": [{"id": 10, "run_attempt": 1, "status": "completed"}],
            },
            "/repos/owner/repo/issues?state=all&sort=created&direction=asc&per_page=100&page=1": [
                {"id": i} for i in range(1, 101)
            ],
        }
    )
    sink = Sink(tmp_path)
    result = collect_github("owner/repo", "", sink, reader=reader, max_pages=1)
    assert any(
        row["reason"] == "inventory_count_mismatch" and row["metadata"]["expected"] == 40000
        for row in sink.gaps
    )
    assert any(row["reason"] == "pagination_limit_exceeded" for row in sink.gaps)
    assert not result["inventories"]["actions_run:/actions/runs"]["complete"]


def test_discussions_comments_and_replies_are_separate_sources(tmp_path):
    class Discussions(Reader):
        def graphql(self, query, variables):
            def connection(rows):
                return {
                    "totalCount": len(rows),
                    "nodes": rows,
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                }

            if "RepositoryDiscussions" in query:
                return {
                    "data": {
                        "repository": {
                            "discussions": connection(
                                [{"id": "D1", "number": 4, "body": "discussion"}]
                            )
                        }
                    }
                }
            if "DiscussionComments" in query:
                return {
                    "data": {
                        "node": {
                            "comments": connection(
                                [{"id": "C1", "body": "comment", "replies": {"totalCount": 1}}]
                            )
                        }
                    }
                }
            return {"data": {"node": {"replies": connection([{"id": "R1", "body": "reply"}])}}}

    sink = Sink(tmp_path)
    result = collect_github("owner/repo", "", sink, reader=Discussions())
    assert result["counts"]["discussion"] == 1
    assert result["counts"]["discussion_comment"] == 1
    assert result["counts"]["discussion_reply"] == 1
    assert b'"parent_comment_id":"C1"' in next(
        row["raw"] for row in sink.records if row["kind"] == "discussion_reply"
    )


def test_budget_records_all_remaining_run_ids(tmp_path):
    class BudgetReader(Reader):
        def json(self, path, *, immutable=False):
            if "/attempts/" in path:
                self.stop_reason = "request_budget_exhausted"
                raise CollectionError(self.stop_reason)
            return super().json(path, immutable=immutable)

    reader = BudgetReader(
        {
            "/repos/owner/repo/actions/runs?per_page=100&page=1": {
                "total_count": 3,
                "workflow_runs": [
                    {"id": number, "run_attempt": 1, "status": "completed"}
                    for number in [10, 11, 12]
                ],
            },
        }
    )
    sink = Sink(tmp_path)
    collect_github("owner/repo", "", sink, reader=reader)
    remaining = next(
        row for row in sink.gaps if row["source_id"] == "actions_run_details:remaining"
    )
    assert remaining["metadata"]["source_ids"] == [11, 12]
    assert remaining["reason"] == "request_budget_exhausted"


def test_resume_rotates_mutable_reviews_and_failed_logs_before_revalidation(tmp_path):
    class SmallBudgetReader(Reader):
        max_requests = 16

        def __init__(self, responses):
            super().__init__(responses)
            self.requests = 0

        def json(self, path, *, immutable=False):
            self.requests += 1
            return super().json(path, immutable=immutable)

        def binary(self, path, *, immutable=False):
            self.requests += 1
            return super().binary(path, immutable=immutable)

        def graphql(self, query, variables):
            self.requests += 1
            return super().graphql(query, variables)

    responses = {
        "/repos/owner/repo/pulls?state=all&sort=created&direction=asc&per_page=100&page=1": [
            {"id": number, "number": number} for number in [1, 2, 3]
        ],
        "/repos/owner/repo/actions/runs?per_page=100&page=1": {
            "total_count": 3,
            "workflow_runs": [
                {"id": number, "run_attempt": 1, "status": "completed"} for number in [10, 11, 12]
            ],
        },
        "/repos/owner/repo/actions/runs/10/attempts/1/logs": CollectionError("github_http_404"),
    }
    first_reader, first_sink = SmallBudgetReader(responses), Sink(tmp_path)
    collect_github("owner/repo", "same-scope", first_sink, reader=first_reader)
    assert any("/pulls/1/reviews?" in path for path, _ in first_reader.calls)
    assert not any("/pulls/2/reviews?" in path for path, _ in first_reader.calls)
    assert any("/runs/10/attempts/1/logs" in path for path, _ in first_reader.binary_calls)
    assert any(gap["reason"] == "github_http_404" for gap in first_sink.gaps)

    second_reader, second_sink = SmallBudgetReader(responses), Sink(tmp_path)
    collect_github("owner/repo", "same-scope", second_sink, reader=second_reader)
    assert any("/pulls/2/reviews?" in path for path, _ in second_reader.calls)
    assert not any("/pulls/1/reviews?" in path for path, _ in second_reader.calls)
    assert any("/runs/11/attempts/1/logs" in path for path, _ in second_reader.binary_calls)
    assert not any("/runs/10/attempts/1/logs" in path for path, _ in second_reader.binary_calls)
    previous = next(
        gap for gap in second_sink.gaps if gap["source_id"] == "pull_request_reviews:remaining"
    )
    assert previous["metadata"]["previously_attempted_source_ids"] == [1]
    assert previous["metadata"]["prior_attempt_state"] == "historical_not_revalidated_this_pass"


def test_corrupt_resume_checkpoint_cannot_hide_source_ids(tmp_path):
    from scripts.repository_corpus_github import _Progress

    progress = _Progress(tmp_path, "owner/repo")
    rows = [{"id": 1}, {"id": 2}, {"id": 3}]
    _, _, digest = progress.rotate("reviews", rows)
    progress.advance("reviews", 1, digest)
    assert _Progress(tmp_path, "owner/repo").rotate("reviews", rows)[0] == rows[1:] + rows[:1]
    progress.path.write_text(
        '{"data":{"repository":"owner/repo","schema":1,"families":{"reviews":{"last_id":2}}},"sha256":"bad"}'
    )
    assert _Progress(tmp_path, "owner/repo").rotate("reviews", rows)[0] == rows


class Response(io.BytesIO):
    headers = {"ETag": '"v1"'}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class Opener:
    def __init__(self, response=b'{"id":1}'):
        self.response = response
        self.calls = []

    def open(self, request, *, timeout):
        self.calls.append(request)
        return Response(self.response)


def test_immutable_cache_hash_and_credential_scope(tmp_path):
    reader = GitHubReader("owner/repo", "token-a", tmp_path, max_requests=2)
    reader.opener = Opener()
    assert reader.json("/repos/owner/repo/actions/runs/1/attempts/1", immutable=True) == {"id": 1}
    assert reader.json("/repos/owner/repo/actions/runs/1/attempts/1", immutable=True) == {"id": 1}
    assert len(reader.opener.calls) == 1
    body = next(reader.cache_dir.glob("*.body"))
    body.write_bytes(b'{"id":2}')
    assert reader.json("/repos/owner/repo/actions/runs/1/attempts/1", immutable=True) == {"id": 1}
    assert len(reader.opener.calls) == 2
    other = GitHubReader("owner/repo", "token-b", tmp_path)
    assert other.cache_dir != reader.cache_dir


def test_reader_limits_and_no_mutations(tmp_path):
    reader = GitHubReader("owner/repo", "token", tmp_path, max_requests=1, max_download_bytes=4)
    reader.opener = Opener(b"12345")
    with pytest.raises(CollectionError, match="source_byte_limit_exceeded"):
        reader.binary("/repos/owner/repo/actions/artifacts/1/zip")
    with pytest.raises(CollectionError, match="request_budget_exhausted"):
        reader.json("/repos/owner/repo")
    with pytest.raises(CollectionError, match="github_write_forbidden"):
        reader.graphql("mutation DeleteStuff { delete }", {})


def test_signed_download_redirect_never_forwards_token():
    request = urllib.request.Request(
        "https://api.github.com/repos/owner/repo/actions/artifacts/1/zip",
        headers={"Authorization": "Bearer sensitive", "If-None-Match": "old"},
    )
    redirect = _DownloadRedirect()
    result = redirect.redirect_request(
        request, None, 302, "found", {}, "https://results.blob.core.windows.net/path?sig=signed"
    )
    assert result.get_header("Authorization") is None
    assert result.get_header("If-none-match") is None
    with pytest.raises(CollectionError, match="download_redirect_not_allowed"):
        redirect.redirect_request(request, None, 302, "found", {}, "https://attacker.example/file")
