"""Real Git/shell regression tests; temporary repositories only, no network."""
import hashlib
import os
from pathlib import Path
import subprocess

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts/redmi_repository_backup.sh"


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def shell(command, *args):
    return subprocess.run(["bash", "-c", 'source "$1"; ' + command, "test", str(SCRIPT), *map(str, args)],
                          text=True, capture_output=True)


def test_pinned_history_survives_force_update_and_gc(tmp_path):
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.invalid")
    (repo / "file").write_text("original")
    git(repo, "add", "file")
    git(repo, "commit", "-qm", "original")
    original = git(repo, "rev-parse", "HEAD")
    assert shell('pin_tips "$2"', repo).returncode == 0
    git(repo, "checkout", "--orphan", "replacement")
    (repo / "file").write_text("replacement")
    git(repo, "add", "file")
    git(repo, "commit", "-qm", "replacement")
    for branch in git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads").splitlines():
        if branch != "replacement":
            git(repo, "branch", "-D", branch)
    assert shell('pin_tips "$2"', repo).returncode == 0
    git(repo, "reflog", "expire", "--expire=now", "--all")
    git(repo, "gc", "--prune=now")
    assert git(repo, "show", original + ":file") == "original"
    assert git(repo, "rev-parse", "refs/archive/tips/" + original) == original


@pytest.mark.parametrize("state", ["valid", "missing", "corrupt", "invalid_oid"])
def test_all_lfs_bytes_are_checked(tmp_path, state):
    data = b"actual LFS bytes\n"
    oid = hashlib.sha256(data).hexdigest()
    obj = tmp_path / "lfs/objects" / oid[:2] / oid[2:4] / oid
    obj.parent.mkdir(parents=True)
    if state != "missing":
        obj.write_bytes(data if state != "corrupt" else b"broken")
    inventory = tmp_path / "oids"
    inventory.write_text((oid if state != "invalid_oid" else "../invalid") + "\n")
    result = shell('verify_lfs "$2" "$3"; printf "%s:%s" "$LFS_COUNT" "$LFS_BYTES"', tmp_path, inventory)
    assert (result.returncode == 0) == (state == "valid")
    if state == "valid":
        assert result.stdout == f"1:{len(data)}"


def test_lfs_inventory_includes_previous_versions(tmp_path):
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.invalid")
    oids = []
    for version in (b"old", b"new"):
        oid = hashlib.sha256(version).hexdigest()
        oids.append(oid)
        (repo / "asset.dat").write_text(f"version https://git-lfs.github.com/spec/v1\noid sha256:{oid}\nsize {len(version)}\n")
        git(repo, "add", "asset.dat")
        git(repo, "commit", "-qm", oid)
    inventory = git(repo, "lfs", "ls-files", "--all", "--long")
    assert all(oid in inventory for oid in oids)


def test_invalid_job_cannot_record_success(tmp_path):
    env = dict(os.environ, CHAT_BACKUP_DIR=str(tmp_path), GITHUB_SHA="invalid", GITHUB_RUN_ID="1",
               GITHUB_REPOSITORY="maksimp6/Chat")
    result = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True)
    assert result.returncode != 0
    assert not (tmp_path / "runner-last-success.json").exists()
