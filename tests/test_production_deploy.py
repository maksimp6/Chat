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
    assert (
        "traefik.http.middlewares.alice-production-https-redirect.redirectscheme.scheme=https"
        in source
    )
    assert "traefik.http.routers.alice-production-https.tls.certresolver=letsencrypt" in source
    assert 'ACME_DIR="$ROOT_DIR/keys/letsencrypt"' in source
    assert 'ACME_FILE="$ACME_DIR/acme.json"' in source
    assert "--certificatesresolvers.letsencrypt.acme.storage=/letsencrypt/acme.json" in source
    docker_block = source.split("docker run -d", 1)[1].split("-e HOST=", 1)[0]
    assert "ALICE_SHORT_TOKEN" not in docker_block


def test_production_deployment_has_no_removed_backend_dependency():
    workflow = (ROOT / ".github" / "workflows" / "production-deploy.yml").read_text(
        encoding="utf-8"
    )
    preview = (ROOT / "deploy" / "preview" / "server.sh").read_text(encoding="utf-8")
    production = SCRIPT.read_text(encoding="utf-8")

    for source in (workflow, preview, production):
        assert ("SUPA" + "BASE_") not in source
        assert ("supa" + "base") not in source.lower()


def _function_source(source, name):
    start = source.index(f"{name}() {{")
    end = source.index("\n}\n", start) + 3
    return source[start:end]


def test_deploy_secrets_survive_the_ssh_stdin_handoff(tmp_path):
    workflow = (ROOT / ".github" / "workflows" / "production-deploy.yml").read_text(
        encoding="utf-8"
    )
    deploy_line = next(
        line.strip()
        for line in workflow.splitlines()
        if "production-server.sh' deploy" in line and line.strip().startswith("printf")
    )

    # Fake ssh runs the remote command locally, like sshd would, with our stdin.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_ssh = bin_dir / "ssh"
    fake_ssh.write_text('#!/usr/bin/env bash\nexec bash -c "${@: -1}"\n', encoding="utf-8")
    fake_ssh.chmod(0o755)

    # The remote script runs the real secret reader and records what it got.
    source = SCRIPT.read_text(encoding="utf-8")
    out = tmp_path / "secrets.env"
    remote = tmp_path / "remote"
    remote.mkdir()
    (remote / "production-server.sh").write_text(
        "set -euo pipefail\n"
        'die() { echo "$*" >&2; exit 1; }\n'
        + _function_source(source, "require_runtime_secret")
        + "require_runtime_secret\n"
        f'printf \'%s\\n\' "$ALICE_SHORT_TOKEN" "$ALICE_GITHUB_CLIENT_ID" '
        f'"$ALICE_GITHUB_CLIENT_SECRET" "$ALICE_GITHUB_ALLOWED_LOGINS" > \'{out}\'\n',
        encoding="utf-8",
    )

    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(tmp_path),
        "PREVIEW_SSH_PORT": "22",
        "PREVIEW_SSH_USER": "deploy",
        "PREVIEW_SSH_HOST": "example.invalid",
        "REMOTE_BASE_DIR": str(remote),
        "ALICE_SHORT_TOKEN": "short-token-value",
        "ALICE_GITHUB_CLIENT_ID": "client-id-value",
        "ALICE_GITHUB_CLIENT_SECRET": "client-secret-value",
        "ALICE_GITHUB_ALLOWED_LOGINS": "maksimp6",
    }
    result = subprocess.run(
        ["bash", "-c", deploy_line], env=env, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert out.read_text(encoding="utf-8").splitlines() == [
        "short-token-value",
        "client-id-value",
        "client-secret-value",
        "maksimp6",
    ]
    assert "short-token-value" not in deploy_line
    assert "$token" not in deploy_line

    # Without GitHub secrets the token still arrives and OAuth stays empty.
    env["ALICE_GITHUB_CLIENT_ID"] = ""
    env["ALICE_GITHUB_CLIENT_SECRET"] = ""
    result = subprocess.run(
        ["bash", "-c", deploy_line], env=env, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert out.read_text(encoding="utf-8").splitlines() == [
        "short-token-value",
        "",
        "",
        "maksimp6",
    ]
