"""Real-Git regressions for the opt-in immutable repository index."""

import copy
import json
from pathlib import Path
import subprocess

import pytest

from scripts import build_ai_index


def git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def write(root, name, content):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def commit(root):
    git(root, "add", ".")
    git(
        root,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-qm",
        "fixture",
    )
    return git(root, "rev-parse", "HEAD")


@pytest.fixture
def repository(tmp_path):
    git(tmp_path, "init", "-q")
    write(tmp_path, "app.py", "def alpha():\n    return 1\n")
    write(tmp_path, "helper.py", "def helper():\n    return 2\n")
    write(
        tmp_path,
        "tests/test_app.py",
        "from app import alpha\n\ndef test_alpha():\n    assert alpha() == 1\n",
    )
    sha = commit(tmp_path)
    return tmp_path, sha


def build(root, sha, **kwargs):
    return build_ai_index.build_index(root, revision=sha, repository="maksimp6/Chat", **kwargs)


def test_reads_committed_blobs_not_dirty_or_untracked_source(repository):
    root, sha = repository
    expected = build(root, sha)
    write(root, "app.py", "this is not valid python!")
    write(root, "untracked.py", "raise RuntimeError('must not execute')\n")
    status = git(root, "status", "--porcelain")
    actual = build(root, sha)
    assert actual == expected
    assert git(root, "status", "--porcelain") == status
    assert {f["path"] for f in actual["files"]} == {"app.py", "helper.py", "tests/test_app.py"}
    assert actual["source_version"] == sha
    assert actual["repository"] == "maksimp6/chat"


def test_source_ids_and_line_refs_bound_to_commit(repository):
    root, sha = repository
    payload = build(root, sha)
    app = next(f for f in payload["files"] if f["path"] == "app.py")
    assert app["source_id"] == "maksimp6/chat:code:app.py"
    assert app["source_ref"] == f"https://github.com/maksimp6/chat/blob/{sha}/app.py"
    assert app["symbols"][0]["source_ref"] == app["source_ref"] + "#L1-L2"
    assert len(app["git_blob_oid"]) == 40
    assert len(app["sha256"]) == 64


def test_unchanged_snapshot_reuses_all_ast_and_is_identical(repository, monkeypatch):
    root, sha = repository
    first = build(root, sha)

    def no_parse(*args, **kwargs):
        pytest.fail("unchanged Git blob was parsed again")

    monkeypatch.setattr(build_ai_index.ast, "parse", no_parse)
    stats = {}
    second = build(root, sha, previous_index=first, stats=stats)
    assert second == first
    assert stats == {"parsed_files": 0, "reused_files": 3, "deleted_files": 0}
    second["files"][0]["symbols"].clear()
    assert first["files"][0]["symbols"]


def test_changed_added_deleted_files_and_updated_refs(repository, monkeypatch):
    root, sha = repository
    previous = build(root, sha)
    original = copy.deepcopy(previous)
    write(root, "app.py", "def beta():\n    return 3\n")
    (root / "helper.py").unlink()
    write(root, "new.py", "def added():\n    return 4\n")
    new_sha = commit(root)
    full = build(root, new_sha)
    calls = []
    parse = build_ai_index.ast.parse

    def count_parse(*args, **kwargs):
        calls.append(kwargs.get("filename"))
        return parse(*args, **kwargs)

    monkeypatch.setattr(build_ai_index.ast, "parse", count_parse)
    stats = {}
    incremental = build(root, new_sha, previous_index=previous, stats=stats)
    assert incremental == full
    assert previous == original
    assert len(calls) == 2
    assert stats == {"parsed_files": 2, "reused_files": 1, "deleted_files": 1}
    assert "helper.py" not in {f["path"] for f in incremental["files"]}
    assert all(new_sha in f["source_ref"] for f in incremental["files"])
    assert all(new_sha in s["source_ref"] for f in incremental["files"] for s in f["symbols"])


@pytest.mark.parametrize(
    "field,value",
    [
        ("repository", "other/chat"),
        ("snapshot_schema_version", 999),
        ("schema_version", 999),
        ("parser_version", "incompatible"),
    ],
)
def test_incompatible_cache_rejected(repository, field, value):
    root, sha = repository
    previous = build(root, sha)
    previous[field] = value
    with pytest.raises(ValueError, match="incompatible"):
        build(root, sha, previous_index=previous)


def test_different_repository_gets_different_ids(repository):
    root, sha = repository
    first = build(root, sha)
    second = build_ai_index.build_index(root, revision=sha, repository="other/Chat")
    assert {f["source_id"] for f in first["files"]}.isdisjoint(
        f["source_id"] for f in second["files"]
    )


def test_symlink_is_not_read_and_is_reported(repository, tmp_path_factory):
    root, _ = repository
    external = tmp_path_factory.mktemp("outside") / "secret.py"
    external.write_text("SENSITIVE_VALUE_DO_NOT_INDEX", encoding="utf-8")
    (root / "link.py").symlink_to(external)
    sha = commit(root)
    payload = build(root, sha)
    assert "link.py" not in {f["path"] for f in payload["files"]}
    assert payload["skipped_paths"] == {"link.py": "non_regular_file"}
    assert "SENSITIVE_VALUE_DO_NOT_INDEX" not in json.dumps(payload)


def test_literals_are_not_serialized_and_source_is_never_executed(repository):
    root, _ = repository
    write(
        root,
        "literal.py",
        "password = 'VALUE_MUST_NOT_BE_INDEXED'\nraise RuntimeError('not executed')\n",
    )
    payload = build(root, commit(root))
    assert "VALUE_MUST_NOT_BE_INDEXED" not in json.dumps(payload)
    assert "literal.py" in {f["path"] for f in payload["files"]}


@pytest.mark.parametrize(
    "kwargs", [{"max_files": 1}, {"max_file_bytes": 5}, {"max_total_bytes": 5}]
)
def test_explicit_limits_fail_without_partial_success(repository, kwargs):
    root, sha = repository
    with pytest.raises(ValueError, match="limit"):
        build(root, sha, **kwargs)


@pytest.mark.parametrize("revision", ["missing-revision", "--all", "HEAD\n--all"])
def test_invalid_revision_does_not_return_an_index(repository, revision):
    root, _ = repository
    with pytest.raises(ValueError, match="revision"):
        build(root, revision)


def test_revision_label_cannot_override_snapshot_provenance(repository):
    root, sha = repository
    payload = build(root, sha)
    result = build_ai_index.query_affected(payload, ["app.py"])
    assert result["provenance"]["git_revision"] == sha
    assert result["provenance"]["repository"] == "maksimp6/chat"
    assert result["provenance"]["files"]["app.py"]["source_ref"].endswith(f"/{sha}/app.py")
    with pytest.raises(ValueError, match="revision"):
        build_ai_index.query_affected(payload, ["app.py"], git_revision="fake")


def test_cli_round_trip_and_atomic_failure(repository, tmp_path_factory):
    root, sha = repository
    output = tmp_path_factory.mktemp("index") / "index.json"
    script = Path(build_ai_index.__file__)
    import sys

    command = [
        sys.executable,
        str(script),
        "--root",
        str(root),
        "--revision",
        sha,
        "--repository",
        "maksimp6/Chat",
        "--output",
        str(output),
        "--compact",
    ]
    subprocess.run(command, check=True, capture_output=True)
    before = output.read_bytes()
    subprocess.run([*command, "--previous-index", str(output)], check=True, capture_output=True)
    assert output.read_bytes() == before
    query = subprocess.run(
        [
            sys.executable,
            str(script),
            "--index-file",
            str(output),
            "--query-affected",
            "app.py",
            "--compact",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "tests/test_app.py" in json.loads(query.stdout)["affected_tests"]
    broken = ["missing" if v == sha else v for v in command]
    result = subprocess.run(broken, capture_output=True, text=True)
    assert result.returncode != 0
    assert output.read_bytes() == before
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("field", ["module", "git_blob_oid", "is_test"])
def test_corrupted_cached_entry_fails_explicitly(repository, field):
    root, sha = repository
    cached = build(root, sha)
    del cached["files"][0][field]
    with pytest.raises(ValueError, match="incompatible"):
        build(root, sha, previous_index=cached)


def test_warm_cache_does_not_bypass_limits(repository):
    root, sha = repository
    cached = build(root, sha)
    with pytest.raises(ValueError, match="limit"):
        build(root, sha, previous_index=cached, max_file_bytes=1)


@pytest.mark.parametrize("source", ["from missing syntax", "# coding: unknown-encoding\npass\n"])
def test_invalid_source_does_not_leak_source_text(repository, source):
    root, _ = repository
    write(root, "bad.py", source + "\n# SENSITIVE_FAILURE_TEXT\n")
    sha = commit(root)
    with pytest.raises(ValueError, match="cannot parse Python source") as error:
        build(root, sha)
    assert "SENSITIVE_FAILURE_TEXT" not in str(error.value)


def test_escaped_paths_keep_real_source_refs(repository):
    root, _ = repository
    write(root, "space #/module.py", "def hello():\n    return 1\n")
    sha = commit(root)
    payload = build(root, sha)
    entry = next(f for f in payload["files"] if f["path"] == "space #/module.py")
    assert entry["source_ref"].endswith(f"/{sha}/space%20%23/module.py")


def retrieval_query(sha, repository="maksimp6/Chat"):
    from agent_retrieval.models import RetrievalQuery

    return RetrievalQuery(
        text="alpha",
        repository=repository,
        work_item="issue:538",
        branch="test",
        head_sha=sha,
        role="backend",
        skills_version="test",
    )


def test_existing_retriever_returns_snapshot_source_link(repository):
    from agent_retrieval.service import _code_hits

    root, sha = repository
    payload = build(root, sha)
    hits = _code_hits(retrieval_query(sha), payload, sha, "maksimp6/Chat")
    assert hits
    assert all(f"/blob/{sha}/" in hit.ref for hit in hits)


@pytest.mark.parametrize(
    "field,value", [("source_version", "a" * 40), ("repository", "other/chat")]
)
def test_retrieval_cannot_relabel_another_snapshot_as_current(repository, field, value):
    from agent_retrieval.service import _code_hits

    root, sha = repository
    payload = build(root, sha)
    payload[field] = value
    assert _code_hits(retrieval_query(sha), payload, sha, "maksimp6/Chat") == []


def test_partial_clone_is_rejected_before_implicit_fetch(repository):
    root, sha = repository
    git(root, "config", "remote.origin.promisor", "true")
    with pytest.raises(ValueError, match="partial clone"):
        build(root, sha)
