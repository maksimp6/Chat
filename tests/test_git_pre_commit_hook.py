import os
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install_git_hooks.sh"
HOOK_TEMPLATE = ROOT / "scripts" / "pre_commit_hook.sh"


def _run(*args, cwd):
    return subprocess.run(
        [*args],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )


def _prepare_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    assert _run("git", "init", "-q", cwd=repo).returncode == 0
    scripts = repo / "scripts"
    scripts.mkdir()
    shutil.copy2(INSTALLER, scripts / INSTALLER.name)
    shutil.copy2(HOOK_TEMPLATE, scripts / HOOK_TEMPLATE.name)
    return repo


def test_pre_commit_template_uses_only_canonical_formatter():
    source = HOOK_TEMPLATE.read_text(encoding="utf-8")

    assert "alice-pro-canonical-format-hook" in source
    assert "bash scripts/format.sh check" in source
    assert "bash scripts/format.sh write" in source
    assert "ruff format" not in source
    assert "prettier" not in source.lower()


def test_hook_installer_copies_executable_hook_into_git_dir(tmp_path):
    repo = _prepare_repo(tmp_path)

    result = _run("bash", "scripts/install_git_hooks.sh", cwd=repo)

    assert result.returncode == 0, result.stderr
    hook_path = Path(
        _run("git", "rev-parse", "--git-path", "hooks/pre-commit", cwd=repo).stdout.strip()
    )
    if not hook_path.is_absolute():
        hook_path = repo / hook_path

    assert hook_path.exists()
    assert os.access(hook_path, os.X_OK)
    assert "alice-pro-canonical-format-hook" in hook_path.read_text(encoding="utf-8")


def test_hook_installer_refuses_to_replace_foreign_hook_without_force(tmp_path):
    repo = _prepare_repo(tmp_path)
    hook_path = repo / ".git" / "hooks" / "pre-commit"
    hook_path.write_text("#!/bin/sh\necho foreign\n", encoding="utf-8")

    result = _run("bash", "scripts/install_git_hooks.sh", cwd=repo)

    assert result.returncode == 1
    assert "existing foreign pre-commit hook" in result.stderr
    assert hook_path.read_text(encoding="utf-8") == "#!/bin/sh\necho foreign\n"


def test_installed_hook_runs_formatter_check_from_repository_root(tmp_path):
    repo = _prepare_repo(tmp_path)
    marker = repo / "format-check-ran"
    (repo / "scripts" / "format.sh").write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        '[[ "$1" == "check" ]]\n'
        f"printf ok > {marker.name!r}\n",
        encoding="utf-8",
    )

    install = _run("bash", "scripts/install_git_hooks.sh", cwd=repo)
    assert install.returncode == 0, install.stderr

    nested = repo / "nested"
    nested.mkdir()
    result = _run("../.git/hooks/pre-commit", cwd=nested)

    assert result.returncode == 0, result.stderr
    assert marker.read_text(encoding="utf-8") == "ok"
