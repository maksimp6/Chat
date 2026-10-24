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




