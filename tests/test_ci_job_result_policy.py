"""Exercise the real finalizer and its wiring without waiting thirty seconds."""

import json
import os
import subprocess
import time
from pathlib import Path

import pytest
import yaml

from scripts import ci_job_result_policy as policy


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "scripts/ci_job_result_policy.py").read_text()
JOBS = [
    (path.name, name, job)
    for path in sorted((ROOT / ".github/workflows").glob("*.yml"))
    for name, job in yaml.safe_load(path.read_text())["jobs"].items()
]


@pytest.mark.parametrize("elapsed_ns", [29_999_000_000, 30_000_000_000, 30_001_000_000])
@pytest.mark.parametrize("outcome", ["success", "failure", "skipped", "cancelled"])
def test_finalizer_boundary(monkeypatch, tmp_path, capsys, elapsed_ns, outcome):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("JOB_STARTED_NS", "100")
    monkeypatch.setenv("JOB_STEP_RESULTS", json.dumps({"test": {"outcome": outcome}}))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setattr(policy.time, "monotonic_ns", lambda: 100 + elapsed_ns)
    failed = outcome in {"failure", "cancelled"}
    overridden = failed and elapsed_ns > 30_000_000_000
    assert policy.main() == int(failed and not overridden)
    output = capsys.readouterr().out
    assert ("::warning::" in output) is overridden
    assert f"overridden={str(overridden).lower()}" in summary.read_text()
    assert "Policy PASS does not mean VERIFIED" in summary.read_text()
    assert ("original failures=test" in output) is failed


def test_invalid_clock_is_not_overridden(monkeypatch, tmp_path):
    monkeypatch.setenv("JOB_STARTED_NS", "100")
    monkeypatch.setattr(policy.time, "monotonic_ns", lambda: 99)
    with pytest.raises(ValueError, match="Invalid job clock"):
        policy.main()
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("elapsed_seconds,expected", [(0, 1), (31, 0)])
def test_embedded_finalizer_runs_in_bash(tmp_path, elapsed_seconds, expected):
    """Run the actual YAML command using synthetic elapsed time, not Actions E2E."""
    finalizer = JOBS[0][2]["steps"][-1]
    summary = tmp_path / "summary.md"
    outcomes = {
        "job_policy_clock": {"outcome": "success", "outputs": {}},
        "failed_check": {"outcome": "failure", "outputs": {"secret": "SYNTHETIC_CANARY"}},
        "dependent_check": {"outcome": "skipped", "outputs": {}},
    }
    result = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", finalizer["run"]],
        env={
            **os.environ,
            "JOB_STARTED_NS": str(time.monotonic_ns() - elapsed_seconds * 1_000_000_000),
            "JOB_STEP_RESULTS": json.dumps(outcomes),
            "GITHUB_STEP_SUMMARY": str(summary),
        },
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == expected, result.stderr
    assert "original failures=failed_check." in result.stdout
    assert "SYNTHETIC_CANARY" not in result.stdout + result.stderr + summary.read_text()


@pytest.mark.parametrize("filename,name,job", JOBS, ids=[f"{f}:{n}" for f, n, _ in JOBS])
def test_every_job_has_a_finalizer(filename, name, job):
    steps = job["steps"]
    if filename == "logos-once.yml":
        # The owner requested one visible step. Its EXIT trap is exercised with
        # real Bash failures and the same duration boundary in test_logos_once.
        assert name == "logos" and len(steps) == 1
        step = steps[0]
        assert step["name"] == "logos" and "if" not in step
        assert "continue-on-error" not in step
        assert "trap finish EXIT" in step["run"]
        assert "execution_exit=" in step["run"]
        assert "Policy PASS does not mean VERIFIED." in step["run"]
        return
    clock, finalizer = steps[0], steps[-1]
    assert clock["id"] == "job_policy_clock"
    assert "time.monotonic_ns()" in clock["run"]
    assert "continue-on-error" not in clock
    assert finalizer["id"] == "job_policy_result"
    assert finalizer["if"] == "${{ !cancelled() }}"
    assert "continue-on-error" not in finalizer
    assert finalizer["run"] == "python3 - <<'PY'\n" + SCRIPT + "PY\n"
    assert finalizer["env"] == {
        "JOB_STARTED_NS": "${{ steps.job_policy_clock.outputs.started_ns }}",
        "JOB_STEP_RESULTS": "${{ toJSON(steps) }}",
    }
    identifiers = [step["id"] for step in steps]
    assert len(identifiers) == len(set(identifiers))
    assert all(step["continue-on-error"] is True for step in steps[1:-1])
    for step in steps[1:-1]:
        condition = step["if"]
        if "always()" not in condition:
            assert "!cancelled()" in condition
            assert "!contains(steps.*.outcome, 'failure')" in condition
            assert "!contains(steps.*.outcome, 'cancelled')" in condition
