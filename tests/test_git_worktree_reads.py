"""Read-only Git tool behavior on real checkouts, linked worktrees and locks."""

import os
from pathlib import Path
import subprocess
import time

import pytest

from tool_providers import git as git_tools


def git(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def repository(tmp_path):
    root = tmp_path / "repository"
    root.mkdir()
    git(root, "init", "-q")
    (root / "tracked.txt").write_text("fixture\n", encoding="utf-8")
    git(root, "add", ".")
    git(
        root,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-qm",
        "fixture",
    )
    return root


@pytest.fixture
def worktree(repository, tmp_path):
    root = tmp_path / "linked"
    git(repository, "worktree", "add", "-q", "-b", "linked", str(root))
    assert (root / ".git").is_file()
    return root


@pytest.mark.parametrize("name", ["git_status", "git_log", "git_diff", "git_branches"])
def test_read_tools_support_linked_worktree(worktree, name):
    before = git(worktree, "status", "--porcelain")
    result = getattr(git_tools, name)({"repo_path": str(worktree), "limit": 2}, {})
    assert result["success"], result
    assert result["is_bare"] is False
    assert git(worktree, "status", "--porcelain") == before


@pytest.mark.parametrize("name", ["git_status", "git_log", "git_diff", "git_branches"])
@pytest.mark.parametrize("layout", ["checkout", "worktree", "bare"])
def test_reads_preserve_old_writer_lock(repository, worktree, tmp_path, name, layout):
    root = {"checkout": repository, "worktree": worktree}.get(layout)
    if root is None:
        root = tmp_path / "bare.git"
        git(repository, "clone", "--bare", "-q", str(repository), str(root))
    lock = Path(git(root, "rev-parse", "--path-format=absolute", "--git-path", "index.lock"))
    lock.write_bytes(b"synthetic-writer-lock")
    old = time.time() - 60
    os.utime(lock, (old, old))
    before = lock.stat()
    result = getattr(git_tools, name)({"repo_path": str(root), "limit": 2}, {})
    # Diff against a working tree is unavailable in a bare repository; even its
    # error must leave a concurrent writer's lock intact.
    assert result["success"] is (not (layout == "bare" and name == "git_diff")), result
    assert lock.read_bytes() == b"synthetic-writer-lock"
    assert lock.stat().st_mtime_ns == before.st_mtime_ns
    assert lock.stat().st_ino == before.st_ino


def test_invalid_git_file_fails_without_mutation(tmp_path):
    (tmp_path / ".git").write_text("invalid git directory\n", encoding="utf-8")
    original = (tmp_path / ".git").read_bytes()
    result = git_tools.git_status({"repo_path": str(tmp_path)}, {})
    assert result["success"] is False
    assert (tmp_path / ".git").read_bytes() == original
