from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy" / "preview" / "server.sh"


def test_preview_server_script_is_valid_bash():
    result = subprocess.run(
        ["bash", "-n", str(SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_preview_server_uses_tokenized_https_path_routing_and_strip():
    source = SCRIPT.read_text(encoding="utf-8")
    assert 'tokenized_prefix="/$ALICE_SHORT_TOKEN' in source
    assert 'tokenized_rule="PathPrefix(' in source
    assert 'TRAEFIK_IMAGE="traefik:v3.7.13"' in source
    assert "-p 0.0.0.0:80:80" in source
    assert "-p 0.0.0.0:443:443" in source
    assert 'entrypoints.websecure.address=:443' in source
    assert 'tls.certresolver=letsencrypt' in source
    assert 'stripprefixregex.regex=^/[^/]+' in source
    assert '-e ALICE_REQUIRE_SHORT_TOKEN=1' in source
    assert '-e ALICE_SHORT_TOKEN="$ALICE_SHORT_TOKEN"' in source
    assert '-e ALICE_PREVIEW_BASE_PATH=' in source


def test_preview_workflow_uses_vps_deployer():
    workflow = (ROOT / ".github" / "workflows" / "preview-deploy.yml").read_text(
        encoding="utf-8"
    )
    assert "deploy/preview/server.sh" in workflow
    assert "PREVIEW_SSH_HOST" in workflow
    assert "PREVIEW_PUBLIC_BASE_URL" in workflow
    assert "ALICE_SHORT_TOKEN" in workflow
    assert "Public HTTPS health and TLS check" in workflow
    assert "Verify VPS routing" in workflow
