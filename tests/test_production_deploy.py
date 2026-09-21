from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy" / "production" / "server.sh"


def test_production_server_script_is_valid_bash():
    result = subprocess.run(
        ["bash", "-n", str(SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_production_deployment_uses_runtime_secret_not_traefik_labels():
    source = SCRIPT.read_text(encoding="utf-8")
    assert "ALICE_SHORT_TOKEN" in source
    assert '--env-file "$runtime_env"' in source
    assert "maxxxpavlov.ru" in source
    assert "maxxxpavlov.online" in source
    assert "!PathPrefix" in source
    assert "!PathRegexp" in source
    assert "^/[^/]+/preview/" in source
    assert "/preview/" in source
    assert "traefik.http.routers.alice-production-https.tls=true" in source
    assert "traefik.http.middlewares.alice-production-https-redirect.redirectscheme.scheme=https" in source
    assert "traefik.http.routers.alice-production-https.tls.certresolver=letsencrypt" in source
    docker_block = source.split('docker run -d', 1)[1].split('-e HOST=', 1)[0]
    assert 'ALICE_SHORT_TOKEN' not in docker_block