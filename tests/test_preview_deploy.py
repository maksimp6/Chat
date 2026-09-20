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


def test_preview_server_uses_path_prefix_routing_and_strip():
    source = SCRIPT.read_text(encoding="utf-8")
    assert r"Path(\`$base_path\`) || PathPrefix(\`$base_path/\`)" in source
    assert 'TRAEFIK_IMAGE="traefik:v3.7.13"' in source
    assert 'traefik.http.middlewares.${container}-strip.stripprefix.prefixes=$base_path' in source
    assert 'ALICE_PREVIEW_BASE_PATH="$base_path"' in source


def test_preview_workflow_uses_vps_deployer():
    workflow = (ROOT / ".github" / "workflows" / "preview-deploy.yml").read_text(
        encoding="utf-8"
    )
    assert "deploy/preview/server.sh" in workflow
    assert "PREVIEW_SSH_HOST" in workflow
    assert "PREVIEW_PUBLIC_BASE_URL" in workflow
    assert "Path-prefix" not in workflow
    assert 'printf "%s/alice-preview" "$HOME"' in workflow
    assert "Verify VPS routing" in workflow
