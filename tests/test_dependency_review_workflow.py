"""Dependency review must fail closed without relying on writable PR tokens."""

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_dependency_review_gate_covers_all_prs_and_vulnerability_scopes():
    workflow = yaml.safe_load((ROOT / ".github/workflows/dependency-review.yml").read_text())
    # PyYAML interprets the YAML 1.1 `on` key as True.
    assert workflow[True] == {"pull_request": None}
    assert workflow["permissions"] == {"contents": "read", "pull-requests": "write"}
    job = workflow["jobs"]["dependency-review"]
    assert job["name"] == "Review dependency vulnerabilities"
    assert "if" not in job
    assert "continue-on-error" not in job
    assert "needs" not in job
    assert len(job["steps"]) == 1
    scan = job["steps"][0]
    assert scan["uses"] == (
        "actions/dependency-review-action@a1d282b36b6f3519aa1f3fc636f609c47dddb294"
    )
    assert "if" not in scan
    assert "continue-on-error" not in scan
    config = scan["with"]
    assert config["warn-only"] is False
    assert config["fail-on-severity"] == "low"
    assert {scope.strip() for scope in config["fail-on-scopes"].split(",")} == {
        "runtime",
        "development",
        "unknown",
    }
    assert config.get("vulnerability-check", True) is True
    assert not config.get("allow-ghsas")
    assert "config-file" not in config
    assert config["comment-summary-in-pr"] == "always"
    assert config["show-openssf-scorecard"] is False
