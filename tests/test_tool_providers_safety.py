import errno
import io
import os
import shutil
import stat
import subprocess
import zipfile
from types import SimpleNamespace

from tool_providers import filesystem, git as git_tools
from tool_providers.termux_system import SYSTEM_TOOLS


def _unrestricted(monkeypatch, tmp_path):
    monkeypatch.setattr(filesystem, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(filesystem, "BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setattr(filesystem, "restrictions_lifted", lambda: True)


def _real_hardlink(link, target):
    """Use an existing native ln when Android's Python omits os.link.

    The fixture must create a real shared inode. A filesystem refusal remains
    a failing test; neither a copy, a symlink nor a mocked inode is substituted.
    """
    native_required = False
    try:
        link.hardlink_to(target)
    except (AttributeError, NotImplementedError, io.UnsupportedOperation):
        native_required = True
    except OSError as exc:
        if exc.errno not in (errno.ENOSYS, errno.ENOTSUP):
            raise
        native_required = True
    if native_required:
        executable = shutil.which("ln")
        if executable is None and os.access("/system/bin/ln", os.X_OK):
            executable = "/system/bin/ln"
        assert executable is not None, "Python hard links are unavailable and no existing native ln was found"
        result = subprocess.run(
            [executable, "--", str(target), str(link)],
            capture_output=True, text=True, timeout=5, check=False,
        )
        assert result.returncode == 0, (
            "Native hard-link creation failed (no skip or substitute): "
            + (result.stderr or result.stdout)[:500]
        )
    source_stat, link_stat = target.stat(), link.stat()
    assert not link.is_symlink(), "The fixture must create a hard link, not a symlink"
    assert (source_stat.st_dev, source_stat.st_ino) == (link_stat.st_dev, link_stat.st_ino)
    assert source_stat.st_nlink >= 2 and link_stat.st_nlink >= 2


def test_write_file_keeps_the_mode_of_the_replaced_file(monkeypatch, tmp_path):
    _unrestricted(monkeypatch, tmp_path)
    script = tmp_path / "run.sh"
    script.write_text("echo old\n")
    script.chmod(0o755)

    result = filesystem.write_file({"path": str(script), "content": "echo new\n"})

    assert "error" not in result
    assert stat.S_IMODE(script.stat().st_mode) == 0o755


def test_delete_path_removes_the_symlink_not_its_target(monkeypatch, tmp_path):
    _unrestricted(monkeypatch, tmp_path)
    target = tmp_path / "important.txt"
    target.write_text("keep")
    link = tmp_path / "link.txt"
    link.symlink_to(target)

    result = filesystem.delete_path({"path": str(link)})

    assert "error" not in result
    assert target.read_text() == "keep"
    assert not link.is_symlink()


def test_zip_compress_refuses_to_write_over_its_source(monkeypatch, tmp_path):
    _unrestricted(monkeypatch, tmp_path)
    source = tmp_path / "data.txt"
    source.write_text("precious")

    result = filesystem.zip_compress({"source_path": str(source), "archive_path": str(source)})

    assert "error" in result
    assert source.read_text() == "precious"


def test_system_tools_remain_registered_with_existing_private_approvals():
    for name in ("list_sms", "send_sms", "take_camera_photo", "list_contacts", "get_wifi_status"):
        assert name in SYSTEM_TOOLS
    for name in ("get_device_location", "list_sms", "send_sms", "take_camera_photo", "list_contacts"):
        assert SYSTEM_TOOLS[name]["requires_approval"] is True
    assert "get_system_battery" in SYSTEM_TOOLS


def test_delete_path_keeps_leaf_credential_protection(monkeypatch, tmp_path):
    monkeypatch.setattr(filesystem, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(filesystem, "restrictions_lifted", lambda: False)
    for name in (".env", "private.key", "state.memory", "id_ed25519"):
        target = tmp_path / name
        target.write_text("keep")
        result = filesystem.delete_path({"path": name})
        assert "error" in result
        assert target.read_text() == "keep"


def test_zip_compress_refuses_a_hard_link_to_its_source(monkeypatch, tmp_path):
    _unrestricted(monkeypatch, tmp_path)
    source = tmp_path / "data.txt"
    source.write_text("precious")
    archive = tmp_path / "archive.zip"
    _real_hardlink(archive, source)

    result = filesystem.zip_compress({"source_path": str(source), "archive_path": str(archive)})

    assert "error" in result
    assert source.read_text() == "precious"


def test_zip_inside_its_source_excludes_output_and_temp_aliases(monkeypatch, tmp_path):
    _unrestricted(monkeypatch, tmp_path)
    source = tmp_path / "data"
    source.mkdir()
    (source / "precious.txt").write_text("keep")
    archive = source / "archive.zip"
    archive.write_bytes(b"previous archive")
    archive.chmod(0o640)
    alias = source / "old-archive-alias.zip"
    _real_hardlink(alias, archive)
    original_walk = filesystem.os.walk

    def walk_with_temp_alias(path):
        temporary = next(source.glob(".zip_*.tmp"))
        _real_hardlink(source / "temp-alias.zip", temporary)
        yield from original_walk(path)

    monkeypatch.setattr(filesystem.os, "walk", walk_with_temp_alias)

    result = filesystem.zip_compress({"source_path": str(source), "archive_path": str(archive)})

    assert result.get("success") is True, result
    assert result["files_archived"] == 1
    with zipfile.ZipFile(archive) as created:
        assert created.namelist() == ["precious.txt"]
        assert created.read("precious.txt") == b"keep"
    assert alias.read_bytes() == b"previous archive"
    assert stat.S_IMODE(archive.stat().st_mode) == 0o640
    assert list(source.glob(".zip_*.tmp")) == []


def test_zip_read_failure_preserves_existing_output_and_cleans_temp(monkeypatch, tmp_path):
    _unrestricted(monkeypatch, tmp_path)
    source = tmp_path / "data.txt"
    source.write_text("precious")
    archive = tmp_path / "archive.zip"
    archive.write_bytes(b"previous archive")

    def cannot_read(*args, **kwargs):
        raise OSError("source became unreadable")

    monkeypatch.setattr(filesystem.zipfile.ZipFile, "write", cannot_read)

    result = filesystem.zip_compress({"source_path": str(source), "archive_path": str(archive)})

    assert "error" in result
    assert source.read_text() == "precious"
    assert archive.read_bytes() == b"previous archive"
    assert list(tmp_path.glob(".zip_*.tmp")) == []


def test_index_lock_is_left_to_git_without_blocking_unrelated_fetch(monkeypatch, tmp_path):
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    lock = git_dir / "index.lock"
    lock.write_text("owned by Git")
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout="fetched", stderr="")

    monkeypatch.setattr(git_tools, "_resolve_repo_path", lambda *args: str(tmp_path))
    monkeypatch.setattr(git_tools, "_is_bare_repo", lambda path: False)
    monkeypatch.setattr(git_tools.subprocess, "run", run)

    result = git_tools._run_git(["fetch", "origin"])

    assert result["success"] is True
    assert commands == [["git", "-C", str(tmp_path), "fetch", "origin"]]
    assert lock.read_text() == "owned by Git"


def test_failed_staging_never_commits_the_existing_index(monkeypatch):
    commands = []

    def run(args, *unused, **kwargs):
        commands.append(args)
        return {"success": False, "error": "staging failed"}

    monkeypatch.setattr(git_tools, "_run_git", run)

    result = git_tools.git_commit({"message": "new", "files": ["missing.txt"]}, {})

    assert result == {"success": False, "error": "staging failed"}
    assert commands == [["add", "--", "missing.txt"]]
