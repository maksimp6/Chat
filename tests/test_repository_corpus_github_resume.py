"""A global read limit must not advance the cursor past an unfinished item."""

import hashlib
import io
import json
import re
import urllib.error
import zipfile
from urllib.parse import parse_qs, urlsplit

import pytest

from scripts import repository_corpus_github as github


class Sink:
    repository = "owner/repo"

    def __init__(self, directory):
        self.cache_dir = directory
        self.records = []
        self.gaps = []

    def add(self, kind, source_id, url, version, raw, metadata=None):
        self.records.append({"kind": kind, "source_id": source_id, "raw": raw})

    def gap(self, kind, source_id, reason, metadata=None):
        self.gaps.append(
            {"kind": kind, "source_id": source_id, "reason": reason, "metadata": metadata}
        )


def zip_bytes():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("result.txt", "saved output")
    return buffer.getvalue()


class Opener:
    def __init__(self, reader, fail_at=None, failure=None):
        self.reader = reader
        self.fail_at = fail_at
        self.failure = failure
        self.paths = []

    def open(self, request, timeout):
        assert timeout == 45
        parsed = urlsplit(request.full_url)
        path = parsed.path
        self.paths.append(path + ("?" + parsed.query if parsed.query else ""))
        if len(self.paths) == self.fail_at:
            if self.failure == "download":
                raw = b"x" * (self.reader.max_total_bytes - self.reader.downloaded_bytes + 1)
                return self.response(raw)
            raise urllib.error.HTTPError(request.full_url, int(self.failure), "", {}, None)
        if path.endswith(("/zip", "/logs")) or re.search(r"/releases/assets/[0-9]+$", path):
            return self.response(zip_bytes())
        query = parse_qs(parsed.query)
        if path.endswith("/reviews"):
            page = int(query["page"][0])
            value = [{"id": number} for number in range(1, 101)] if page == 1 else []
        elif path.endswith("/jobs"):
            value = {"total_count": 1, "jobs": [{"id": 123}]}
        elif path.endswith("/assets"):
            value = [{"id": 20, "name": "one.zip"}, {"id": 21, "name": "two.zip"}]
        else:
            value = {"id": 2, "status": "completed"}
        return self.response(json.dumps(value).encode())

    @staticmethod
    def response(raw):
        response = io.BytesIO(raw)
        response.headers = {"ETag": '"fixed"'}
        return response


FAMILIES = {
    "pull_request_reviews": (github._collect_reviews, 1, 2),
    "actions_run_details": (github._collect_runs, 4, 5),
    "actions_artifact": (github._collect_artifacts, 1, 1),
    "release_assets": (github._collect_releases, 1, 2),
}


def rows():
    return [
        {"id": number, "number": number, "run_attempt": 2, "status": "completed"}
        for number in [1, 2, 3]
    ]


def setup(tmp_path, family, *, maximum=100, fail_at=None, failure=None):
    sink = Sink(tmp_path)
    reader = github.GitHubReader(
        "owner/repo", "test-token", tmp_path, max_requests=maximum, max_total_bytes=100000
    )
    opener = Opener(reader, fail_at, failure)
    reader.opener = opener
    progress = github._Progress(reader.cache_dir, "owner/repo")
    digest = hashlib.sha256(github._encoded([1, 2, 3])).hexdigest()
    progress.advance(family, 1, digest)
    collector = github._Collector("owner/repo", reader, sink, 100, progress)
    return collector, reader, opener, sink


@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.parametrize("failure", ["request", "429", "401", "download"])
def test_mid_item_global_limit_preserves_cursor_and_names_current_item(tmp_path, family, failure):
    phase, budget, stop_call = FAMILIES[family]
    collector, reader, opener, sink = setup(
        tmp_path,
        family,
        maximum=budget if failure == "request" else 100,
        fail_at=None if failure == "request" else stop_call,
        failure=failure,
    )
    if family == "actions_artifact" and failure == "request":
        reader.json("/repos/owner/repo/warmup")
    phase(collector, rows())
    expected_reason = {
        "request": "request_budget_exhausted",
        "429": "github_rate_limit",
        "401": "github_http_401",
        "download": "download_budget_exhausted",
    }[failure]
    assert reader.stop_reason == expected_reason
    saved = github._Progress(reader.cache_dir, "owner/repo")
    assert saved.families[family]["last_id"] == 1
    deferred = next(gap for gap in sink.gaps if gap["source_id"] == family + ":remaining")
    assert deferred["reason"] == expected_reason
    assert deferred["metadata"]["source_ids"] == [2, 3, 1]
    assert len(opener.paths) <= stop_call


def test_resume_retries_same_run_attempt_and_reuses_completed_immutable_reads(tmp_path):
    collector, reader, _, sink = setup(tmp_path, "actions_run_details", maximum=4)
    github._collect_runs(collector, rows())
    assert reader.stop_reason == "request_budget_exhausted"
    resumed_reader = github.GitHubReader("owner/repo", "test-token", tmp_path, max_requests=100)
    opener = Opener(resumed_reader)
    resumed_reader.opener = opener
    progress = github._Progress(resumed_reader.cache_dir, "owner/repo")
    resumed = github._Collector("owner/repo", resumed_reader, sink, 100, progress)
    github._collect_runs(resumed, rows())
    assert opener.paths[0] == "/repos/owner/repo/actions/runs/2/attempts/2/jobs?per_page=100&page=1"
    assert not any(
        "/runs/2/attempts/1/" in path or path.endswith("/runs/2/attempts/1")
        for path in opener.paths
    )
    assert resumed_reader.cache_hits >= 4
    assert any(
        record["kind"] == "actions_logs" and "/runs/2/attempts/2/logs" in record["source_id"]
        for record in sink.records
    )


@pytest.mark.parametrize("family", FAMILIES)
def test_endpoint_404_is_visible_and_can_advance(tmp_path, family):
    phase, _, stop_call = FAMILIES[family]
    collector, reader, _, sink = setup(tmp_path, family, fail_at=stop_call, failure="404")
    phase(collector, rows()[1:2])
    assert reader.stop_reason is None
    assert any(gap["reason"] == "github_http_404" for gap in sink.gaps)
    assert github._Progress(reader.cache_dir, "owner/repo").families[family]["last_id"] == 2
    assert (
        any("2" in record["source_id"] for record in sink.records) or family == "actions_artifact"
    )


def test_expired_artifact_advances_without_download(tmp_path):
    collector, reader, opener, sink = setup(tmp_path, "actions_artifact")
    github._collect_artifacts(collector, [{"id": 2, "expired": True}])
    assert not opener.paths and reader.stop_reason is None
    assert (
        github._Progress(reader.cache_dir, "owner/repo").families["actions_artifact"]["last_id"]
        == 2
    )
    assert sink.gaps[0]["reason"] == "artifact_expired"
