"""Dependabot policy (#927): grouped routine updates, visible security updates."""

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
UPDATES = CONFIG["updates"]


def _directories(update):
    return update.get("directories") or [update["directory"]]














def test_no_npm_manifest_is_left_unmanaged():
    assert not list(ROOT.rglob("package-lock.json"))


def test_policy_is_documented():
    doc = (ROOT / "docs" / "development" / "dependency-updates.md").read_text(encoding="utf-8")
    assert ".github/dependabot.yml" in doc
    assert "tests/test_dependabot_policy.py" in doc
