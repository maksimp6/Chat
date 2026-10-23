"""Bounded collection passes retain evidence and share the existing private cache."""

import io
import json
import urllib.error

import pytest

from scripts import repository_corpus_github as github
from scripts import repository_corpus_git as git
from scripts import repository_rag as rag
from scripts import repository_rag_resume as resume


def arguments(tmp_path, *extra):
    return rag._arguments(
        [
            "--repository",
            "owner/repo",
            "--archive-dir",
            str(tmp_path / "archive"),
            "--receipt",
            str(tmp_path / "receipt.json"),
            *extra,
        ]
    )


def summary(reader):
    return {
        "status": "partial",
        "requests": reader.requests,
        "downloaded_bytes": reader.downloaded_bytes,
        "cache_hits": reader.cache_hits,
        "generation_requests": 0,
    }


def add_source(sink, identity):
    sink.add("issue", identity, "https://github.com/owner/repo/issues/1", "v1", b"evidence")


class Opener:
    def __init__(self, responses):
        self.responses = responses
        self.paths = []

    def open(self, request, timeout):
        assert timeout == 45
        self.paths.append(request.full_url)
        item = self.responses(request)
        if isinstance(item, Exception):
            raise item
        response = io.BytesIO(item)
        response.headers = {"ETag": '"example"'}
        return response


def test_pass_argument_is_bounded_and_defaults_to_one(tmp_path):
    assert arguments(tmp_path).github_passes == 1
    assert arguments(tmp_path, "--github-passes", "3").github_passes == 3
    for value in ("0", "4", "-1", "1.5"):
        with pytest.raises(SystemExit):
            arguments(tmp_path, "--github-passes", value)


def test_three_passes_collect_git_once_upload_once_and_keep_all_evidence(tmp_path, monkeypatch):
    args = arguments(tmp_path, "--github-passes", "3", "--upload")
    archive = rag.CorpusArchive(args.archive_dir, args.repository)
    archive.gap("prior", "old", "historical_gap")
    calls, caches, epochs = [], [], []

    def collect_git(root, sink):
        calls.append("git")
        add_source(sink, "git")

    def collect_github(repository, token, sink, *, reader, epoch):
        number = len(caches) + 1
        calls.append("github")
        caches.append(reader.cache_dir)
        epochs.append(epoch)
        assert token == "test-token"
        assert sink is archive
        add_source(sink, f"pass-{number}")
        sink.gap("issue", str(number), f"pass_{number}_gap")
        github._Progress(reader.cache_dir, repository).advance("reviews", number, "snapshot")
        return summary(reader)

    def upload(sink, *unused):
        calls.append("upload")
        assert len(sink.entries) == 4
        return {"documents": 4}

    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    monkeypatch.setattr(git, "collect_git", collect_git)
    monkeypatch.setattr(github, "collect_github", collect_github)
    monkeypatch.setattr(rag, "upload_archive", upload)
    monkeypatch.setattr(rag, "verify_search", lambda *args: {"status": "verified_fragments"})
    result = {}
    rag._run(args, archive, result)
    assert calls == ["git", "github", "github", "github", "upload"]
    assert len(set(caches)) == 1
    assert len(set(epochs)) == 1
    state = result["github_collection_passes"]
    assert state["epoch"] == epochs[0]
    assert state["completed"] == 3
    assert state["terminal_reason"] == "pass_limit_reached"
    assert state["terminal_kind"] == "configured_limit"
    for number, record in enumerate(state["passes"], 1):
        assert record["pass"] == number and record["new_source_versions"] == 1
        assert record["started_at"] <= record["finished_at"]
        assert record["gaps"][0]["reason"] == f"pass_{number}_gap"
        assert record["progress_after"]["reviews"]["last_id"] == number
    assert state["passes"][0]["progress_before"] == {}
    assert state["passes"][1]["progress_before"] == state["passes"][0]["progress_after"]
    assert [gap["reason"] for gap in archive.gaps] == [
        "historical_gap",
        "pass_1_gap",
        "pass_2_gap",
        "pass_3_gap",
    ]
    saved = json.loads((archive.directory / "github-collection-passes.json").read_bytes())
    assert saved == state
    assert result["github_collection"] == state["passes"][-1]["collection"]
    assert result["status"] == "indexed_partial"


@pytest.mark.parametrize(
    ("http_status", "headers", "reason", "kind", "expected_passes"),
    [
        (401, {}, "github_http_401", "observed_access_failure", 1),
        (429, {}, "github_rate_limit", "observed_rate_limit", 1),
        (403, {"X-RateLimit-Remaining": "0"}, "github_rate_limit", "observed_rate_limit", 1),
        (403, {}, "pass_limit_reached", "configured_limit", 3),
    ],
)
def test_observed_global_limits_stop_but_endpoint_denial_does_not(
    tmp_path, monkeypatch, http_status, headers, reason, kind, expected_passes
):
    args = arguments(tmp_path, "--github-passes", "3")
    archive = rag.CorpusArchive(args.archive_dir, args.repository)
    attempts = []
    opener = Opener(
        lambda request: urllib.error.HTTPError(request.full_url, http_status, "", headers, None)
    )

    def collect(repository, token, sink, *, reader, epoch):
        attempts.append(reader)
        reader.opener = opener
        add_source(sink, str(len(attempts)))
        with pytest.raises(github.CollectionError) as error:
            reader.json("/repos/owner/repo/issues")
        sink.gap("issue", "1", str(error.value))
        return summary(reader)

    monkeypatch.setattr(github, "collect_github", collect)
    result = {}
    rag._collect_github_passes(args, archive, result)
    state = result["github_collection_passes"]
    assert len(attempts) == expected_passes
    assert len(opener.paths) == expected_passes
    assert state["terminal_reason"] == reason
    assert state["terminal_kind"] == kind


def test_request_budget_can_continue_and_immutable_cache_is_reused(tmp_path, monkeypatch):
    args = arguments(tmp_path, "--github-passes", "3", "--github-max-requests", "2")
    archive = rag.CorpusArchive(args.archive_dir, args.repository)
    opener = Opener(lambda request: b'{"id":1}')
    readers = []

    def collect(repository, token, sink, *, reader, epoch):
        readers.append(reader)
        reader.opener = opener
        assert reader.json("/repos/owner/repo/common", immutable=True) == {"id": 1}
        add_source(sink, str(len(readers)))
        with pytest.raises(github.CollectionError, match="request_budget_exhausted"):
            for number in range(3):
                reader.json(f"/repos/owner/repo/pass-{len(readers)}/{number}", immutable=True)
        sink.gap("details", "remaining", reader.stop_reason)
        return summary(reader)

    monkeypatch.setattr(github, "collect_github", collect)
    result = {}
    rag._collect_github_passes(args, archive, result)
    state = result["github_collection_passes"]
    assert state["completed"] == 3
    assert state["terminal_reason"] == "pass_limit_reached"
    assert [reader.cache_hits for reader in readers] == [0, 1, 1]
    assert sum(path.endswith("/common") for path in opener.paths) == 1
    assert len(opener.paths) == 6
    assert state["downloaded_bytes"] == 6 * len(b'{"id":1}')
    assert all(
        record["reader_stop_reason"] == "request_budget_exhausted" for record in state["passes"]
    )
    assert all(record["stop_reason"] is None for record in state["passes"])


def test_download_allowance_is_shared_across_passes_and_stops_when_exhausted(tmp_path, monkeypatch):
    args = arguments(tmp_path, "--github-passes", "3")
    archive = rag.CorpusArchive(args.archive_dir, args.repository)
    readers = []
    opener = Opener(lambda request: b'{"id":1}')
    monkeypatch.setattr(github, "MAX_TOTAL_BYTES", 12)

    def collect(repository, token, sink, *, reader, epoch):
        readers.append(reader)
        reader.opener = opener
        add_source(sink, str(len(readers)))
        try:
            reader.json(f"/repos/owner/repo/{len(readers)}")
        except github.CollectionError as error:
            sink.gap("issue", "1", str(error))
        return summary(reader)

    monkeypatch.setattr(github, "collect_github", collect)
    result = {}
    rag._collect_github_passes(args, archive, result)
    state = result["github_collection_passes"]
    assert [reader.max_total_bytes for reader in readers] == [12, 4]
    assert state["completed"] == 2
    assert state["terminal_reason"] == "download_budget_exhausted"
    assert state["terminal_kind"] == "configured_limit"
    # The existing reader consumes one sentinel byte to detect overflow.
    assert state["downloaded_bytes"] == 13


def test_repeated_sources_without_cursor_advancement_stop_as_no_progress(tmp_path, monkeypatch):
    args = arguments(tmp_path, "--github-passes", "3")
    archive = rag.CorpusArchive(args.archive_dir, args.repository)
    add_source(archive, "unchanged")

    def collect(repository, token, sink, *, reader, epoch):
        add_source(sink, "unchanged")
        sink.gap("issue", "1", "still_unavailable")
        return summary(reader)

    monkeypatch.setattr(github, "collect_github", collect)
    result = {}
    rag._collect_github_passes(args, archive, result)
    state = result["github_collection_passes"]
    assert state["completed"] == 1
    assert state["terminal_reason"] == "no_collection_progress"
    assert state["terminal_kind"] == "no_progress"
    assert len(archive.entries) == 1


def test_empty_review_cursor_advancement_is_progress_but_inventory_hash_churn_is_not(
    tmp_path, monkeypatch
):
    args = arguments(tmp_path, "--github-passes", "3")
    archive = rag.CorpusArchive(args.archive_dir, args.repository)
    calls = []

    def collect(repository, token, sink, *, reader, epoch):
        calls.append(reader)
        progress = github._Progress(reader.cache_dir, repository)
        progress.advance("reviews", 10, f"inventory-{len(calls)}")
        return summary(reader)

    monkeypatch.setattr(github, "collect_github", collect)
    result = {}
    rag._collect_github_passes(args, archive, result)
    state = result["github_collection_passes"]
    assert state["completed"] == 2
    assert not state["passes"][0]["new_source_versions"]
    assert state["passes"][0]["stop_reason"] is None
    assert state["terminal_reason"] == "no_collection_progress"


@pytest.mark.parametrize("mode", ["--provider-check", "--resume-receipt", "--upload-only"])
def test_check_resume_and_upload_only_do_not_collect_regardless_of_pass_argument(
    tmp_path, monkeypatch, mode
):
    extra = [mode]
    if mode == "--resume-receipt":
        extra.append(str(tmp_path / "prior.json"))
    args = arguments(tmp_path, "--github-passes", "3", *extra)
    archive = rag.CorpusArchive(args.archive_dir, args.repository)
    calls = []

    def forbidden(*args, **kwargs):
        pytest.fail("collector or unintended upload was called")

    monkeypatch.setattr(git, "collect_git", forbidden)
    monkeypatch.setattr(github, "collect_github", forbidden)
    monkeypatch.setattr(rag, "upload_archive", forbidden)
    monkeypatch.setattr(rag, "provider_inventory", lambda *args: calls.append("check") or {})
    monkeypatch.setattr(resume, "resume_from_receipt", lambda *args: calls.append("resume") or {})
    monkeypatch.setattr(rag, "verify_search", lambda *args: {"status": "verified_fragments"})
    if mode == "--upload-only":
        monkeypatch.setattr(rag, "upload_archive", lambda *args: calls.append("upload") or {})
    result = {}
    rag._run(args, archive, result)
    assert calls == [
        {"--provider-check": "check", "--resume-receipt": "resume", "--upload-only": "upload"}[mode]
    ]
    assert "github_collection_passes" not in result
    assert not (archive.directory / "github-collection-passes.json").exists()
