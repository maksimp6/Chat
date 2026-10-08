"""Executable contracts for the standalone Memory DB Make interface."""

from pathlib import Path
import shutil
import subprocess

import pytest

MAKEFILE = Path(__file__).resolve().parents[1] / "memory-db-package" / "Makefile"


@pytest.fixture
def make_dir(tmp_path):
    """Run targets against an isolated copy, never delete real build artifacts."""
    if shutil.which("make") is None:
        pytest.skip("GNU Make is unavailable")
    shutil.copy2(MAKEFILE, tmp_path / "Makefile")
    return tmp_path


def run_make(directory, *arguments):
    return subprocess.run(
        ["make", "--no-print-directory", "-C", str(directory), *arguments],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )


@pytest.mark.parametrize("target", ["build", "test", "clean"])
def test_make_targets_exist(make_dir, target):
    result = run_make(make_dir, "-n", target)
    assert result.returncode == 0, result.stderr


def test_build_uses_project_packager_not_custom_compiler(make_dir):
    result = run_make(make_dir, "-n", "build")
    assert result.returncode == 0
    assert "-m pip wheel --no-deps" in result.stdout
    assert "--wheel-dir dist" in result.stdout


def test_test_target_includes_all_required_suites(make_dir):
    result = run_make(make_dir, "-n", "test")
    assert result.returncode == 0
    for filename in (
        "test_memory_explicit_commit_contract.py",
        "test_memory_v1_acceptance.py",
        "test_memory_store.py",
        "test_memory_standalone_boundary.py",
    ):
        assert filename in result.stdout


@pytest.mark.parametrize("target", ["build", "test"])
def test_failed_underlying_command_fails_make(make_dir, target):
    """Make must propagate failures, never claim a green build or test."""
    result = run_make(make_dir, f"PYTHON={make_dir / 'missing-python'}", target)
    assert result.returncode != 0


def test_clean_removes_only_package_dist(make_dir):
    dist = make_dir / "dist"
    dist.mkdir()
    (dist / "sample.whl").write_bytes(b"artifact")
    unrelated = make_dir / "keep.txt"
    unrelated.write_text("preserve", encoding="utf-8")
    result = run_make(make_dir, "clean")
    assert result.returncode == 0, result.stderr
    assert not dist.exists()
    assert unrelated.read_text(encoding="utf-8") == "preserve"


def test_targets_are_phony(make_dir):
    """An existing file called build or test must not suppress its recipe."""
    for target in ("build", "test"):
        (make_dir / target).touch()
        result = run_make(make_dir, "-n", target)
        assert result.returncode == 0, result.stderr
        assert "-m " in result.stdout
