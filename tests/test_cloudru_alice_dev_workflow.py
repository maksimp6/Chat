"""A deploy input change must not silently leave Alice Dev on an old image."""

from fnmatch import fnmatchcase
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ".github/workflows/cloudru-rdc-mcp-candidate.yml"


@pytest.mark.parametrize(
    "path",
    [
        WORKFLOW,
        "cloud/base.py",
        "cloud/cloudru/client.py",
        "cloud/cloudru/container_apps_client.py",
        "cloud/cloudru/registry_client.py",
        "cloudru_iam.py",
        "requirements-deploy.txt",
        ".python-version",
        "scripts/cloudru_rdc_mcp_candidate.py",
        "scripts/cloudru_deploy.py",
        "scripts/cloudru_browser_probe.py",
        "config/alice/alice-dev-delete-ids.txt",
        "deploy/remote-desktop-commander/Dockerfile.mcp",
        "deploy/remote-desktop-commander/config.json",
        "deploy/remote-desktop-commander/dependency-smoke.cjs",
        "deploy/remote-desktop-commander/dev-bootstrap.sh",
        "deploy/remote-desktop-commander/credential-handoff.mjs",
    ],
)
def test_production_deploy_observes_its_real_inputs(path):
    workflow = yaml.safe_load((ROOT / WORKFLOW).read_text())
    triggers = workflow.get("on", workflow.get(True))
    assert (ROOT / path).is_file()
    assert any(fnmatchcase(path, pattern) for pattern in triggers["push"]["paths"])


def test_minimal_import_preflight_runs_before_cloud_operations():
    job = yaml.safe_load((ROOT / WORKFLOW).read_text())["jobs"]["deploy"]
    names = [step.get("name", "") for step in job["steps"]]
    assert names.index("Validate minimal deploy environment") < names.index(
        "Deploy isolated candidate"
    )
