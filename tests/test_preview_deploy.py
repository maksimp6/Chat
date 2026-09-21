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


def test_preview_server_uses_tokenized_path_routing_and_strip():
    source = SCRIPT.read_text(encoding="utf-8")
    assert "PathRegexp(`^/[^/]+${base_path}(?:/.*)?$`)" in source
    assert 'TRAEFIK_IMAGE="traefik:v3.7.13"' in source
    assert '-p 0.0.0.0:80:80' in source
    assert '-p 0.0.0.0:443:443' in source
    assert "grep -Fq '0.0.0.0:80'" in source
    assert "grep -Fq '0.0.0.0:443'" in source
    assert "traefik.http.middlewares.${container}-token-strip.replacepathregex.regex=$tokenized_replace_regex" in source
    assert "traefik.http.middlewares.${container}-token-strip.replacepathregex.replacement=\\$1\\$2" in source
    assert 'traefik.http.middlewares.${container}-strip.stripprefix.prefixes=$base_path' in source
    assert 'ALICE_REQUIRE_SHORT_TOKEN=1' in source
    assert 'ALICE_SHORT_TOKEN="$ALICE_SHORT_TOKEN"' in source
    assert 'ALICE_PREVIEW_BASE_PATH="/$ALICE_SHORT_TOKEN$base_path"' in source
    assert "--entrypoints.websecure.address=:443" in source
    assert "--providers.file.directory=/etc/traefik/dynamic" in source
    assert 'CERT_SOURCE_DIR="${ALICE_TLS_CERT_DIR:-$ROOT_DIR/certs}"' in source
    assert '"$CERT_SOURCE_DIR:/etc/traefik/certs:ro"' in source
    assert "ensure-traefik" in source


def test_preview_workflow_passes_runtime_token_to_vps_deployer():
    workflow = (ROOT / ".github" / "workflows" / "preview-deploy.yml").read_text(
        encoding="utf-8"
    )
    assert "deploy/preview/server.sh" in workflow
    assert "PREVIEW_SSH_HOST" in workflow
    assert "PREVIEW_PUBLIC_BASE_URL" in workflow
    assert "ALICE_SHORT_TOKEN" in workflow
    assert "printf" in workflow and "ALICE_SHORT_TOKEN" in workflow
    assert "Path-prefix" not in workflow
    assert 'printf "%s/alice-preview" "$HOME"' in workflow
    assert "Verify VPS routing" in workflow
