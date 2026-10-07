"""Regression contract for #538 / PR #950; imports real repository modules."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from agent_retrieval.models import RetrievalQuery
from agent_retrieval.service import HybridRetriever, _code_hits
from scripts import build_ai_index


REPOSITORY = "maksimp6/Chat"
IDENTITY_FIELDS = (
    "schema_version",
    "snapshot_schema_version",
    "source_kind",
    "source_version",
    "repository",
    "parser_version",
)
SNAPSHOT_FIELDS = (*IDENTITY_FIELDS[1:], "skipped_paths")
MISSING = object()


def _query(revision: str) -> RetrievalQuery:
    return RetrievalQuery(
        text="alpha",
        repository=REPOSITORY,
        work_item="issue:538",
        branch="fix/538-revision-index-reuse",
        head_sha=revision,
        role="backend-engineer",
        skills_version="test-v1",
    )


def _git(root: Path, *args: str) -> str:
    env = os.environ.copy()
    env.update(
        GIT_AUTHOR_DATE="2026-10-07T00:00:00Z",
        GIT_COMMITTER_DATE="2026-10-07T00:00:00Z",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
    )
    return subprocess.check_output(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        env=env,
        text=True,
        stderr=subprocess.STDOUT,
    ).strip()


@pytest.fixture(scope="module")
def repository(tmp_path_factory):
    root = tmp_path_factory.mktemp("provenance_git")
    _git(root, "init", "-q")
    (root / "app.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests/test_app.py").write_text(
        "from app import alpha\n\ndef test_alpha():\n    assert alpha() == 1\n",
        encoding="utf-8",
    )
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "fixture A")
    old_sha = _git(root, "rev-parse", "HEAD")
    old = build_ai_index.build_index(root, revision=old_sha, repository=REPOSITORY)
    (root / "app.py").write_text("def alpha():\n    return 2\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "fixture B")
    new_sha = _git(root, "rev-parse", "HEAD")
    new = build_ai_index.build_index(root, revision=new_sha, repository=REPOSITORY)
    return root, old_sha, new_sha, old, new


@pytest.fixture
def snapshot(repository):
    return copy.deepcopy(repository[3])


def _assert_rejected(payload, revision):
    assert _code_hits(_query(revision), payload, revision, REPOSITORY) == []
    with pytest.raises(ValueError):
        build_ai_index.query_affected(payload, ["app.py"], git_revision=revision)


@pytest.mark.parametrize("field", IDENTITY_FIELDS)
@pytest.mark.parametrize("value", [MISSING, None], ids=["missing", "null"])
def test_incomplete_identity_is_rejected(repository, snapshot, field, value):
    if value is MISSING:
        snapshot.pop(field)
    else:
        snapshot[field] = value
    _assert_rejected(snapshot, repository[1])


@pytest.mark.parametrize("field", ["schema_version", "snapshot_schema_version"])
@pytest.mark.parametrize("value", [0, 999, True, 1.0, "1", [], {}])
def test_unsupported_or_coerced_schema_is_rejected(repository, snapshot, field, value):
    snapshot[field] = value
    _assert_rejected(snapshot, repository[1])


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_kind", "filesystem"),
        ("source_kind", []),
        ("source_version", ""),
        ("source_version", "HEAD"),
        ("source_version", "a" * 39),
        ("source_version", 123),
        ("repository", ""),
        ("repository", "not/a/repo"),
        ("repository", "Maksimp6/Chat"),
        ("parser_version", ""),
        ("parser_version", []),
    ],
)
def test_invalid_identity_values_are_rejected(repository, snapshot, field, value):
    snapshot[field] = value
    _assert_rejected(snapshot, repository[1])


@pytest.mark.parametrize("field", ["snapshot_schema_version", "source_version"])
def test_partial_old_snapshot_cannot_be_relabelled(repository, snapshot, field):
    snapshot.pop(field)
    _assert_rejected(snapshot, repository[2])


@pytest.mark.parametrize("keep", ["source_ref", "source_id", "git_blob_oid", "symbol_ref"])
def test_nested_provenance_cannot_downgrade_to_legacy(repository, snapshot, keep):
    for name in SNAPSHOT_FIELDS:
        snapshot.pop(name)
    for item in snapshot["files"]:
        for name in ("source_ref", "source_id", "git_blob_oid"):
            if name != keep:
                item.pop(name)
        for symbol in item["symbols"]:
            if keep != "symbol_ref":
                symbol.pop("source_ref")
    _assert_rejected(snapshot, repository[2])


def test_malformed_nested_file_entry_is_rejected_as_snapshot_metadata(repository):
    payload = {"schema_version": 1, "files": ["not-a-file-record"]}
    _assert_rejected(payload, repository[2])


def test_malformed_nested_symbol_entry_is_rejected_as_snapshot_metadata(repository):
    payload = {"schema_version": 1, "files": [{"symbols": ["not-a-symbol-record"]}]}
    _assert_rejected(payload, repository[2])


def test_real_current_snapshot_has_consistent_provenance(repository):
    _, _, revision, _, payload = repository
    hits = _code_hits(_query(revision), payload, revision, REPOSITORY)
    assert hits
    assert all(f"/blob/{revision}/" in hit.ref for hit in hits)
    assert all(hit.metadata["index_version"] == revision for hit in hits)
    impact = build_ai_index.query_affected(payload, ["app.py"])
    assert impact["provenance"]["git_revision"] == revision
    assert impact["provenance"]["repository"] == REPOSITORY.lower()
    assert impact["affected_tests"] == ["tests/test_app.py"]


def test_complete_stale_snapshot_is_rejected(repository, snapshot):
    _assert_rejected(snapshot, repository[2])


@pytest.mark.parametrize("omit_schema", [False, True])
def test_true_legacy_remains_compatible(repository, omit_schema):
    root, _, revision, _, _ = repository
    payload = build_ai_index.build_index(root)
    if omit_schema:
        payload.pop("schema_version")
    hits = _code_hits(_query(revision), payload, revision, REPOSITORY)
    assert hits and all("/blob/" not in hit.ref for hit in hits)
    result = build_ai_index.query_affected(payload, ["app.py"], git_revision="caller-label")
    assert result["provenance"]["git_revision"] == "caller-label"


def test_consumer_allows_other_python_parser_tag(repository, snapshot):
    snapshot["parser_version"] = "python-ast-v1:cpython-314"
    revision = repository[1]
    assert _code_hits(_query(revision), snapshot, revision, REPOSITORY)
    assert (
        build_ai_index.query_affected(snapshot, ["app.py"])["provenance"]["git_revision"]
        == revision
    )


def test_full_and_incremental_build_remain_identical(repository):
    root, _, revision, previous, expected = repository
    stats: dict[str, int] = {}
    actual = build_ai_index.build_index(
        root,
        revision=revision,
        repository=REPOSITORY,
        previous_index=previous,
        stats=stats,
    )
    assert actual == expected
    assert stats == {"parsed_files": 1, "reused_files": 1, "deleted_files": 0}


def test_cli_rejects_partial_snapshot_without_replacing_output(repository, snapshot, tmp_path):
    snapshot.pop("source_version")
    snapshot["repository"] = "password=do-not-leak-fixture"
    input_path, output_path = tmp_path / "index.json", tmp_path / "result.json"
    input_path.write_text(json.dumps(snapshot), encoding="utf-8")
    output_path.write_text("previous-result\n", encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            build_ai_index.__file__,
            "--query-affected",
            "app.py",
            "--index-file",
            str(input_path),
            "--git-revision",
            repository[2],
            "--output",
            str(output_path),
        ],
        text=True,
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 2
    assert result.stdout == ""
    assert "Traceback" not in result.stderr
    assert "do-not-leak-fixture" not in result.stderr
    assert output_path.read_text(encoding="utf-8") == "previous-result\n"


class _EmptyMemory:
    """Boundary double: this test intentionally covers code retrieval, not memory."""

    def list_records(self, **kwargs):
        return []


def test_hybrid_retriever_rejects_bad_code_and_accepts_fresh_code(repository, snapshot):
    revision = repository[2]
    snapshot.pop("snapshot_schema_version")
    retriever = HybridRetriever(
        memory_store=_EmptyMemory(),
        repository_index=snapshot,
        repository_index_version=revision,
        repository_index_repository=REPOSITORY,
    )
    assert retriever.retrieve(_query(revision)).status == "miss"
    retriever.repository_index = repository[4]
    result = retriever.retrieve(_query(revision))
    assert result.status == "hit"
    assert result.source_counts["code"] > 0
    assert all(f"/blob/{revision}/" in hit.ref for hit in result.hits)
