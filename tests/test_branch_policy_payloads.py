"""Proposed native settings must preserve protection and use real check names."""

import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def _payload(rule_id):
    return json.loads((ROOT / f"docs/development/rulesets/{rule_id}.json").read_text())


def _rule(payload, kind):
    return next(rule for rule in payload["rules"] if rule["type"] == kind)


def test_payloads_preserve_protection_and_enable_thread_resolution():
    for rule_id in (23532452, 24841115):
        payload = _payload(rule_id)
        assert payload["enforcement"] == "active"
        assert payload["bypass_actors"] == []
        assert _rule(payload, "deletion")
        assert _rule(payload, "non_fast_forward")
        parameters = _rule(payload, "pull_request")["parameters"]
        assert parameters["required_review_thread_resolution"] is True
        assert parameters["required_approving_review_count"] == 0
        assert set(parameters["allowed_merge_methods"]) == {"merge", "squash", "rebase"}


def test_master_retains_legacy_checks_and_adds_real_admission_producers():
    payload = _payload(23532452)
    assert payload["conditions"]["ref_name"] == {"exclude": [], "include": ["~DEFAULT_BRANCH"]}
    assert _rule(payload, "required_linear_history")
    assert _rule(payload, "code_scanning")["parameters"]["code_scanning_tools"] == [
        {
            "tool": "CodeQL",
            "security_alerts_threshold": "high_or_higher",
            "alerts_threshold": "errors",
        }
    ]
    checks = _rule(payload, "required_status_checks")["parameters"]
    assert checks["strict_required_status_checks_policy"] is True
    contexts = {item["context"] for item in checks["required_status_checks"]}
    assert {
        "Application tests",
        "PostgreSQL integration",
        "Android debug APK",
        "CI required",
        "Auto-format repository",
        "Trivy repository scan",
        "Zizmor GitHub Actions audit",
        "CodeQL (python)",
        "CodeQL (javascript-typescript)",
        "Offline startup and restart smoke",
        "Merge readiness snapshot",
    } == contexts
    assert all(item["integration_id"] == 15368 for item in checks["required_status_checks"])
    names = set()
    for path in (ROOT / ".github/workflows").glob("*.yml"):
        workflow = yaml.safe_load(path.read_text())
        for job in workflow.get("jobs", {}).values():
            name = job.get("name")
            if name:
                names.add(name)
    # Matrix expansion of the two CodeQL languages is validated separately below.
    assert contexts - {"CodeQL (python)", "CodeQL (javascript-typescript)"} <= names
    codeql = yaml.safe_load((ROOT / ".github/workflows/codeql.yml").read_text())
    analyze = codeql["jobs"]["analyze"]
    assert analyze["name"] == "CodeQL (${{ matrix.language }})"
    assert set(analyze["strategy"]["matrix"]["language"]) == {"python", "javascript-typescript"}


def test_develop_is_lightweight_and_snapshot_has_no_self_dependency():
    payload = _payload(24841115)
    assert payload["conditions"]["ref_name"] == {"exclude": [], "include": ["refs/heads/develop"]}
    checks = _rule(payload, "required_status_checks")["parameters"]
    assert checks["strict_required_status_checks_policy"] is False
    assert checks["required_status_checks"] == [{"context": "CI required", "integration_id": 15368}]
    workflow = yaml.safe_load((ROOT / ".github/workflows/merge-readiness.yml").read_text())
    assert (
        "Merge readiness snapshot"
        not in workflow["jobs"]["readiness"]["env"]["MERGE_REQUIRED_CHECKS"]
    )
