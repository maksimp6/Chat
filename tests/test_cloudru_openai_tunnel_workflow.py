"""Contract for .github/workflows/cloudru-openai-tunnel.yml (persistent tunnel service)."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "cloudru-openai-tunnel.yml"


def _load():
    data = yaml.safe_load(WORKFLOW.read_text())
    return data, data.get("on", data.get(True))


def test_manual_only_with_bounded_actions():
    data, trigger = _load()
    assert set(trigger) == {"workflow_dispatch"}
    options = trigger["workflow_dispatch"]["inputs"]["action"]["options"]
    assert set(options) == {"deploy", "status", "estimate"}
    assert data["permissions"] == {"contents": "read"}


def test_deploy_is_a_separate_single_instance_service():
    data, _ = _load()
    job = data["jobs"]["tunnel"]
    assert job["environment"] == "production"
    env = job["env"]
    assert env["CLOUDRU_CONTAINER_NAME"] == "alice-openai-tunnel"
    assert env["CLOUDRU_MIN_INSTANCES"] == "1"  # the tunnel must keep polling
    assert env["CLOUDRU_MAX_INSTANCES"] == "1"


def test_secrets_go_to_the_container_by_name_only():
    text = WORKFLOW.read_text()
    for name in ("OPENAI_TUNNEL_ID", "OPENAI_TUNNEL_API_KEY", "ALICE_SHORT_TOKEN"):
        assert f"secrets.{name}" in text
        assert f"--env {name}" in text
    assert "--env ALICE_MCP_BEARER_TOKEN" in text
    # The Cloud.ru control-plane key never reaches the application container.
    assert "--env CLOUDRU_IAM_KEY" not in text
    # No acceptance cleanup: this service is meant to stay up.
    assert "cleanup" not in text


def test_actions_are_pinned_by_sha():
    for line in WORKFLOW.read_text().splitlines():
        if "uses:" in line:
            ref = line.split("@", 1)[1].split()[0]
            assert len(ref) == 40, line
