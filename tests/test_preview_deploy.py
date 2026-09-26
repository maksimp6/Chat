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
    assert 'local tokenized_prefix="/$ALICE_SHORT_TOKEN${base_path}"' in source
    assert "local tokenized_rule=" in source
    assert "PathPrefix(" in source
    assert "$tokenized_prefix" in source
    assert 'local http_router="${container}-http"' in source
    assert 'local https_ru_router="${container}-https-ru"' in source
    assert 'local https_online_router="${container}-https-online"' in source
    assert "https_ru_rule" in source and "maxxxpavlov.ru" in source
    assert "https_online_rule" in source and "maxxxpavlov.online" in source
    assert "traefik.http.routers.${http_router}.entrypoints=web" in source
    assert "traefik.http.routers.${https_ru_router}.entrypoints=websecure" in source
    assert "traefik.http.routers.${https_online_router}.entrypoints=websecure" in source
    assert "traefik.http.routers.${https_ru_router}.tls.certresolver=letsencrypt" in source
    assert "traefik.http.routers.${https_online_router}.tls.certresolver=letsencrypt" in source
    assert 'ALICE_SHORT_TOKEN="$ALICE_SHORT_TOKEN"' in source
    assert 'ALICE_PREVIEW_BASE_PATH="/$ALICE_SHORT_TOKEN$base_path"' in source
    assert "--entrypoints.websecure.address=:443" in source
    assert "--providers.file.directory=/etc/traefik/dynamic" in source
    assert 'ACME_DIR="$ROOT_DIR/keys/letsencrypt"' in source
    assert 'ACME_FILE="$ACME_DIR/acme.json"' in source
    assert "--certificatesresolvers.letsencrypt.acme.storage=/letsencrypt/acme.json" in source
    assert "--certificatesresolvers.letsencrypt.acme.httpchallenge=true" in source
    assert "--certificatesresolvers.letsencrypt.acme.httpchallenge.entrypoint=web" in source
    assert '--certificatesresolvers.letsencrypt.acme.email="$ACME_EMAIL"' in source
    assert '"$ACME_DIR:/letsencrypt"' in source
    assert 'chmod 600 "$ACME_FILE"' in source
    assert "ensure-traefik" in source


def test_preview_workflow_passes_runtime_token_to_vps_deployer():
    workflow = (ROOT / ".github" / "workflows" / "preview-deploy.yml").read_text(encoding="utf-8")
    assert "deploy/preview/server.sh" in workflow
    assert "PREVIEW_SSH_HOST" in workflow
    assert "PREVIEW_PUBLIC_BASE_URL" in workflow
    assert "ALICE_SHORT_TOKEN" in workflow
    assert "printf" in workflow and "ALICE_SHORT_TOKEN" in workflow
    assert "Path-prefix" not in workflow
    assert 'printf "%s/alice-preview" "$HOME"' in workflow
    assert "Verify VPS routing" in workflow
