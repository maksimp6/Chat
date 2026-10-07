"""RED contract for the paid Cloud.ru deployment boundary.

Tests only: no Cloud.ru API calls, no resource creation, no billing.
"""

from pathlib import Path

import yaml


ROOT = Path(__file__).parents[1]
WORKFLOW = ROOT / ".github/workflows/cloudru-deploy.yml"
DEPLOY = ROOT / "scripts/cloudru_deploy.py"


def _workflow():
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def test_paid_deploy_job_is_bounded_to_fifteen_minutes():
    workflow = _workflow()
    assert workflow["jobs"]["cloudru"]["timeout-minutes"] == 15


def test_deploy_workflow_has_explicit_five_ruble_budget_guard_before_mutation():
    text = WORKFLOW.read_text(encoding="utf-8")
    deploy_at = text.index("python scripts/cloudru_deploy.py deploy")
    assert "ALICE_LIVE_MAX_RUB" in text[:deploy_at]
    assert "5" in text[:deploy_at]
    assert "live_budget" in text[:deploy_at]


def test_deploy_workflow_guarantees_cleanup_on_every_terminal_outcome():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "if: always()" in text
    assert "cloudru_deploy.py delete --yes" in text


def test_deploy_boundary_no_longer_requires_postgresql_secret():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "ALICE_DATABASE_URL" not in text


def test_deploy_cli_uses_short_acceptance_idle_window():
    text = DEPLOY.read_text(encoding="utf-8")
    assert 'idle_timeout="60s"' in text or 'idle_timeout = "60s"' in text


def test_deploy_cli_keeps_single_instance_fail_closed():
    text = DEPLOY.read_text(encoding="utf-8")
    assert 'CLOUDRU_MAX_INSTANCES", "1"' in text
