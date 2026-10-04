"""Deterministic checks for the Claude Code ops environment setup script."""

import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "claude_cloud_setup.sh"


def run(tmp_path, steps, extra_env=None, path_prefix=None):
    env = {
        "HOME": str(tmp_path),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
        "CLAUDE_SETUP_BIN": str(tmp_path / "bin"),
        "CLAUDE_SETUP_STEPS": steps,
        "CLAUDE_SETUP_DOCKER_WAIT": "1",
        "PATH": f"{path_prefix}:/usr/bin:/bin" if path_prefix else "/usr/bin:/bin",
        **(extra_env or {}),
    }
    return subprocess.run(
        ["bash", str(SCRIPT)], cwd=ROOT, env=env, text=True, capture_output=True, check=False
    )


def stub(directory: Path, name: str, body: str) -> None:
    directory.mkdir(exist_ok=True)
    path = directory / name
    path.write_text("#!/bin/sh\n" + body + "\n")
    path.chmod(0o755)


def test_report_lists_variables_without_printing_values(tmp_path):
    secret = "very-secret-value"
    result = run(
        tmp_path,
        "report",
        {"CLOUDRU_IAM_KEY_SECRET": secret, "EDS_API_KEY": secret, "GH_TOKEN": secret},
    )
    assert result.returncode == 0, result.stderr
    assert secret not in result.stdout + result.stderr
    assert "CLOUDRU_IAM_KEY_SECRET: set" in result.stdout
    assert "EDS_PROJECT_ID: missing" in result.stdout
    assert "CLOUDRU_PROJECT_ID: missing" in result.stdout


def test_docker_step_reports_unavailable_daemon_instead_of_hiding_it(tmp_path):
    fake = tmp_path / "fake"
    stub(fake, "docker", "exit 1")
    stub(fake, "dockerd", "exit 1")
    result = run(tmp_path, "docker", path_prefix=fake)
    assert result.returncode == 0, result.stderr
    assert "docker: unavailable" in result.stdout


def test_docker_step_reports_ready_daemon(tmp_path):
    fake = tmp_path / "fake"
    stub(fake, "docker", "exit 0")
    result = run(tmp_path, "docker", path_prefix=fake)
    assert "docker: ready" in result.stdout


def test_tools_always_run_verified_installers_even_when_binaries_exist():
    script = SCRIPT.read_text()
    tools = script.split("if has_step tools; then", 1)[1].split("\nfi\n", 1)[0]
    assert "bash scripts/install_cloud_cli.sh" in tools
    assert 'python3 scripts/install_eds.py --bin-dir "$BIN"' in tools
    assert "command -v eds" not in tools and "command -v gh" not in tools
    assert "sha256sum --check --status" in tools
