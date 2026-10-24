"""Static deployment-hold contract for #939; never contact Cloud.ru."""

from pathlib import Path
from typing import Any

import pytest
import yaml


WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/cloudru-rdc-mcp-candidate.yml"
EXPECTED_GUARD = (
    "${{ github.event_name == 'workflow_dispatch' "
    "&& github.ref == 'refs/heads/master' "
    "&& github.run_attempt == '1' "
    "&& vars.ALICE_DEV_DEPLOY_ENABLED == 'true' "
    "&& inputs.confirm_paid_deploy == true }}"
)


@pytest.fixture(scope="module")
def workflow() -> dict[str, Any]:
    # BaseLoader preserves the GitHub `on` key and scalar spelling.
    document = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert isinstance(document, dict)
    return document


def test_alice_dev_has_no_automatic_deploy_trigger(workflow):
    triggers = workflow.get("on")
    assert isinstance(triggers, dict)
    assert set(triggers) == {"workflow_dispatch"}, (
        "Owner-deleted containers must not be recreated by automatic events"
    )


def test_alice_dev_paid_confirmation_defaults_to_false(workflow):
    dispatch = workflow.get("on", {}).get("workflow_dispatch")
    assert isinstance(dispatch, dict), "Manual deployment must require explicit confirmation"
    confirmation = dispatch.get("inputs", {}).get("confirm_paid_deploy")
    assert isinstance(confirmation, dict)
    assert confirmation.get("type") == "boolean"
    assert confirmation.get("required") == "true"
    assert confirmation.get("default") == "false"
    assert confirmation.get("description", "").strip()


def test_alice_dev_all_jobs_have_manual_master_approval_guard(workflow):
    jobs = workflow.get("jobs")
    assert isinstance(jobs, dict) and jobs
    for name, job in jobs.items():
        assert isinstance(job, dict)
        # Exact fail-closed contract, not a substitute GitHub expression evaluator.
        guard = " ".join(job.get("if", "").split())
        assert guard == EXPECTED_GUARD, (
            f"{name}: require manual/master/first-attempt/repository-hold/confirmation "
            "at job level, before allocating a runner or exposing production secrets"
        )


def test_alice_dev_retains_production_environment_and_read_only_permissions(workflow):
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["jobs"]["deploy"]["environment"] == "production"
    assert 0 < int(workflow["jobs"]["deploy"]["timeout-minutes"]) <= 25


def test_alice_dev_checkout_does_not_accept_an_untrusted_ref(workflow):
    steps = workflow["jobs"]["deploy"]["steps"]
    checkouts = [step for step in steps if step.get("uses", "").startswith("actions/checkout@")]
    assert len(checkouts) == 1
    checkout = checkouts[0]
    revision = checkout["uses"].split("@", 1)[1]
    assert len(revision) == 40 and all(char in "0123456789abcdef" for char in revision)
    assert checkout["with"]["persist-credentials"] == "false"
    assert "ref" not in checkout["with"]
    assert "repository" not in checkout["with"]


def test_alice_dev_preserves_bounded_acceptance_before_manifest(workflow):
    steps = workflow["jobs"]["deploy"]["steps"]
    names = [step.get("name") for step in steps]
    checks = {
        "Verify OAuth discovery": "node oauth-discovery-smoke.mjs",
        "Verify public MCP end to end": "node mcp-gateway-live-smoke.mjs",
    }
    deploy_index = names.index("Deploy isolated candidate")
    manifest_index = names.index("Upload accepted container manifest")
    for name, command in checks.items():
        index = names.index(name)
        assert deploy_index < index < manifest_index
        step = steps[index]
        assert 0 < int(step["timeout-minutes"]) <= 2
        assert step["run"] == command
        assert step.get("continue-on-error", "false") == "false"
        assert "if" not in step
    manifest = steps[manifest_index]
    assert "if" not in manifest
    assert manifest["with"]["if-no-files-found"] == "error"
