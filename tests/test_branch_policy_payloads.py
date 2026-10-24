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




