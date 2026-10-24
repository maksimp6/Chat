"""Immutable Git corpus controls using real repositories and object stores."""

import hashlib
import json
from pathlib import Path
import subprocess

from scripts import repository_corpus_git as collector


class Sink:
    repository = "owner/repository"

    def __init__(self):
        self.sources = []
        self.gaps = []

    def add(self, kind, source_id, url, version, raw, metadata=None):
        self.sources.append(
            dict(
                kind=kind,
                source_id=source_id,
                url=url,
                version=version,
                raw=raw,
                metadata=metadata or {},
            )
        )

    def gap(self, kind, source_id, reason, metadata=None):
        self.gaps.append(
            dict(kind=kind, source_id=source_id, reason=reason, metadata=metadata or {})
        )


def git(root, *args):
    return (
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
        .stdout.decode()
        .strip()
    )


def init(root):
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q")
    git(root, "config", "user.name", "Fixture")
    git(root, "config", "user.email", "fixture@example.invalid")
    git(root, "config", "commit.gpgsign", "false")
    return root


def commit(root, message="fixture"):
    git(root, "add", ".")
    git(root, "commit", "-qm", message)
    return git(root, "rev-parse", "HEAD")


def test_history_deleted_versions_unique_blobs_and_dirty_tree(tmp_path):
    root = init(tmp_path)
    (root / "file.txt").write_text("first version\n")
    (root / "unchanged.txt").write_text("unchanged content\n")
    first = commit(root)
    git(root, "tag", "initial")
    (root / "file.txt").write_text("second version\n")
    second = commit(root)
    (root / "file.txt").unlink()
    third = commit(root)
    (root / "unchanged.txt").write_text("dirty must stay out\n")
    (root / "untracked.txt").write_text("untracked must stay out\n")
    sink = Sink()
    collector.collect_git(root, sink)
    assert not sink.gaps
    assert {s["version"] for s in sink.sources if s["kind"] == "git_commit"} == {
        first,
        second,
        third,
    }
    blobs = [s for s in sink.sources if s["kind"] == "git_blob"]
    assert {s["raw"] for s in blobs} == {
        b"first version\n",
        b"second version\n",
        b"unchanged content\n",
    }
    assert len(blobs) == 3
    change = next(s for s in sink.sources if s["source_id"] == f"git:changes:{third}")
    assert json.loads(change["raw"])["changes"][0]["status"] == "D"
    assert all("/git/blobs/" in s["url"] and s["version"] for s in blobs)


def test_all_branches_merge_metadata_and_tags(tmp_path):
    root = init(tmp_path)
    (root / "base.txt").write_text("base\n")
    first = commit(root)
    branch = git(root, "branch", "--show-current")
    git(root, "checkout", "-qb", "side")
    (root / "side.txt").write_text("side-only text\n")
    side = commit(root)
    git(root, "tag", "-a", "annotated", "-m", "tag evidence")
    git(root, "checkout", "-q", branch)
    (root / "main.txt").write_text("main-only text\n")
    main = commit(root)
    git(root, "merge", "--no-ff", "-qm", "merge", "side")
    merged = git(root, "rev-parse", "HEAD")
    sink = Sink()
    collector.collect_git(root, sink)
    assert not sink.gaps
    assert {s["version"] for s in sink.sources if s["kind"] == "git_commit"} == {
        first,
        side,
        main,
        merged,
    }
    merge = next(s for s in sink.sources if s["source_id"] == f"git:commit:{merged}")
    assert merge["metadata"]["parents"] == [main, side]
    assert any(s["kind"] == "git_tag" and b"tag evidence" in s["raw"] for s in sink.sources)
    assert len([s for s in sink.sources if s["kind"] == "git_blob"]) == 3


def test_sensitive_file_not_read_even_when_blob_has_public_alias(tmp_path, monkeypatch):
    root = init(tmp_path)
    (root / ".env").write_text("NEVER_READ_SECRET_BLOB")
    (root / "public.txt").write_text("NEVER_READ_SECRET_BLOB")
    commit(root)
    oid = git(root, "rev-parse", "HEAD:.env")
    original = collector._read_object

    def read(root, requested, kind, sink):
        assert requested != oid
        return original(root, requested, kind, sink)

    monkeypatch.setattr(collector, "_read_object", read)
    sink = Sink()
    collector.collect_git(root, sink)
    assert {g["reason"] for g in sink.gaps} == {
        "sensitive_path_excluded",
        "blob_also_present_at_sensitive_path",
    }
    assert not any(b"NEVER_READ_SECRET_BLOB" in s["raw"] for s in sink.sources)


def test_symlink_submodule_targets_not_followed(tmp_path):
    root = init(tmp_path / "repo")
    outside = tmp_path / "external-secret.txt"
    outside.write_text("outside sensitive content")
    (root / "link").symlink_to(outside)
    commit(root)
    sha = git(root, "rev-parse", "HEAD")
    git(root, "update-index", "--add", "--cacheinfo", f"160000,{sha},submodule")
    git(root, "commit", "-qm", "submodule")
    sink = Sink()
    collector.collect_git(root, sink)
    assert any(g["reason"] == "submodule_repository_not_collected" for g in sink.gaps)
    assert any(s["kind"] == "git_blob" and s["raw"] == str(outside).encode() for s in sink.sources)
    assert not any(b"outside sensitive content" in s["raw"] for s in sink.sources)


def test_lfs_pointer_is_not_payload_and_verified_local_content_is_distinct(tmp_path):
    root = init(tmp_path)
    content = b"stored LFS document content\n"
    oid = hashlib.sha256(content).hexdigest()
    (root / "document.txt").write_text(
        f"version https://git-lfs.github.com/spec/v1\noid sha256:{oid}\nsize {len(content)}\n"
    )
    commit(root)
    missing = Sink()
    collector.collect_git(root, missing)
    assert any(s["kind"] == "git_lfs_pointer" for s in missing.sources)
    assert any(g["reason"] == "lfs_payload_not_available_locally" for g in missing.gaps)
    path = root / ".git" / "lfs" / "objects" / oid[:2] / oid[2:4] / oid
    path.parent.mkdir(parents=True)
    path.write_bytes(content)
    available = Sink()
    collector.collect_git(root, available)
    assert not available.gaps
    assert any(
        s["kind"] == "git_lfs" and s["raw"] == content and s["version"] == oid
        for s in available.sources
    )
    path.write_bytes(b"tampered")
    tampered = Sink()
    collector.collect_git(root, tampered)
    assert any(g["reason"] == "lfs_payload_hash_or_size_mismatch" for g in tampered.gaps)
    assert not any(s["kind"] == "git_lfs" for s in tampered.sources)


def test_shallow_history_is_visible_and_tip_tree_still_collected(tmp_path):
    origin = init(tmp_path / "origin")
    (origin / "old.txt").write_text("old unchanged\n")
    commit(origin)
    (origin / "new.txt").write_text("new\n")
    commit(origin)
    shallow = tmp_path / "shallow"
    subprocess.run(["git", "clone", "-q", "--depth=1", origin.as_uri(), str(shallow)], check=True)
    sink = Sink()
    collector.collect_git(shallow, sink)
    assert any(g["reason"] == "shallow_history" for g in sink.gaps)
    assert {s["raw"] for s in sink.sources if s["kind"] == "git_blob"} == {
        b"old unchanged\n",
        b"new\n",
    }


def test_partial_clone_is_reported_before_object_reads(tmp_path, monkeypatch):
    root = init(tmp_path)
    (root / "file.txt").write_text("body")
    commit(root)
    git(root, "config", "remote.origin.promisor", "true")

    def fail(*args):
        raise AssertionError("object read could lazily fetch")

    monkeypatch.setattr(collector, "_read_object", fail)
    sink = Sink()
    collector.collect_git(root, sink)
    assert not sink.sources
    assert [g["reason"] for g in sink.gaps] == ["partial_clone_requires_explicit_fetch"]


def test_missing_blob_is_an_explicit_gap(tmp_path):
    root = init(tmp_path)
    (root / "file.txt").write_text("body")
    commit(root)
    oid = git(root, "rev-parse", "HEAD:file.txt")
    (root / ".git" / "objects" / oid[:2] / oid[2:]).unlink()
    sink = Sink()
    collector.collect_git(root, sink)
    assert any(
        g["source_id"] == f"git:blob:{oid}" and g["reason"] == "git_object_unavailable"
        for g in sink.gaps
    )
    assert not any(s["kind"] == "git_blob" for s in sink.sources)


def test_lfs_storage_symlink_is_not_followed(tmp_path):
    root = init(tmp_path / "repo")
    content = b"outside payload"
    oid = hashlib.sha256(content).hexdigest()
    (root / "document.txt").write_text(
        f"version https://git-lfs.github.com/spec/v1\noid sha256:{oid}\nsize {len(content)}\n"
    )
    commit(root)
    outside = tmp_path / "outside-lfs"
    payload = outside / "objects" / oid[:2] / oid[2:4] / oid
    payload.parent.mkdir(parents=True)
    payload.write_bytes(content)
    (root / ".git" / "lfs").symlink_to(outside, target_is_directory=True)
    sink = Sink()
    collector.collect_git(root, sink)
    assert any(g["reason"] == "lfs_object_symlink" for g in sink.gaps)
    assert not any(s["kind"] == "git_lfs" for s in sink.sources)


def test_path_bytes_and_disabled_replacement_objects(tmp_path):
    root = init(tmp_path)
    filename = "Unicode-ж\nwith\ttabs.txt"
    (root / filename).write_text("real committed content")
    commit(root)
    original = git(root, "rev-parse", "HEAD:" + filename)
    replacement = (
        subprocess.run(
            ["git", "-C", str(root), "hash-object", "-w", "--stdin"],
            input=b"replacement must stay out",
            check=True,
            capture_output=True,
        )
        .stdout.decode()
        .strip()
    )
    git(root, "replace", original, replacement)
    sink = Sink()
    collector.collect_git(root, sink)
    assert any(g["reason"] == "replacement_ref_not_followed" for g in sink.gaps)
    blob = next(s for s in sink.sources if s["kind"] == "git_blob")
    assert blob["raw"] == b"real committed content"
    changes = next(
        json.loads(s["raw"])["changes"] for s in sink.sources if s["kind"] == "git_changes"
    )
    assert changes[0]["path"] == filename
    assert "%0A" in changes[0]["source_url_path"] and "%09" in changes[0]["source_url_path"]


def test_oversized_blob_has_visible_gap(tmp_path, monkeypatch):
    root = init(tmp_path)
    (root / "large.txt").write_bytes(b"a" * 1024)
    commit(root)
    monkeypatch.setattr(collector, "MAX_OBJECT_BYTES", 512)
    sink = Sink()
    collector.collect_git(root, sink)
    assert any(
        g["reason"] == "git_object_size_limit" and g["metadata"]["bytes"] == 1024 for g in sink.gaps
    )
    assert not any(s["kind"] == "git_blob" for s in sink.sources)


def test_new_occurrence_does_not_change_immutable_blob_document(tmp_path):
    root = init(tmp_path)
    (root / "a.txt").write_text("same bytes\n")
    commit(root)
    first = Sink()
    collector.collect_git(root, first)
    (root / "b.txt").write_text("same bytes\n")
    commit(root)
    second = Sink()
    collector.collect_git(root, second)
    assert [s for s in first.sources if s["kind"] == "git_blob"] == [
        s for s in second.sources if s["kind"] == "git_blob"
    ]
