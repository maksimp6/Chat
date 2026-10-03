from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import subprocess

import pytest

from cloud.base import CloudProviderError
from scripts import cloudru_deploy


HELPER = Path(__file__).parents[1] / "scripts" / "codex_cloudru_env.sh"
CREDENTIAL_HELPER = Path(__file__).parents[1] / "scripts" / "codex_agent_credentials.sh"


def _clear_cloudru_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "CLOUDRU_IAM_KEY_ID",
        "CLOUDRU_IAM_KEY_SECRET",
        "CLOUDRU_KEY_ID",
        "CLOUDRU_KEY_SECRET",
        "CLOUDRU_PROJECT_ID",
    ):
        monkeypatch.delenv(name, raising=False)


def test_setup_helper_maps_legacy_names_without_tracing_secret(tmp_path: Path) -> None:
    env = os.environ.copy()
    # Keep this subprocess focused on the legacy-name compatibility path even
    # when the runner itself happens to expose canonical Cloud.ru variables.
    for name in (
        "CLOUDRU_IAM_KEY_ID",
        "CLOUDRU_IAM_KEY_SECRET",
        "CLOUDRU_KEY_ID",
        "CLOUDRU_KEY_SECRET",
        "CLOUDRU_PROJECT_ID",
    ):
        env.pop(name, None)
    env.update(
        {
            "HOME": str(tmp_path),
            "XDG_STATE_HOME": str(tmp_path / "state"),
            "CLOUDRU_KEY_ID": "key-id-for-test",
            "CLOUDRU_KEY_SECRET": "secret-value-for-test",
            "CLOUDRU_PROJECT_ID": "project-id-for-test",
        }
    )
    result = subprocess.run(
        [
            "bash",
            "-c",
            f'set -euxo pipefail; source {HELPER}; test "$CLOUDRU_IAM_KEY_ID" = key-id-for-test',
        ],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )

    state_file = tmp_path / "state" / "alice-pro" / "cloudru-codex.json"
    assert json.loads(state_file.read_text(encoding="utf-8")) == {
        "CLOUDRU_IAM_KEY_ID": "key-id-for-test",
        "CLOUDRU_IAM_KEY_SECRET": "secret-value-for-test",
        "CLOUDRU_PROJECT_ID": "project-id-for-test",
    }
    assert stat.S_IMODE(state_file.stat().st_mode) == 0o600
    assert "secret-value-for-test" not in result.stdout
    assert "secret-value-for-test" not in result.stderr


def test_deploy_loader_prefers_protected_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _clear_cloudru_env(monkeypatch)
    state_file = tmp_path / "cloudru-codex.json"
    state_file.write_text(
        json.dumps(
            {
                "CLOUDRU_IAM_KEY_ID": "cached-id",
                "CLOUDRU_IAM_KEY_SECRET": "cached-secret",
                "CLOUDRU_PROJECT_ID": "cached-project",
            }
        ),
        encoding="utf-8",
    )
    state_file.chmod(0o600)
    monkeypatch.setattr(cloudru_deploy, "CODEX_CLOUDRU_STATE_FILE", state_file)

    cloudru_deploy._load_cloudru_credentials()

    assert os.environ["CLOUDRU_IAM_KEY_ID"] == "cached-id"
    assert os.environ["CLOUDRU_IAM_KEY_SECRET"] == "cached-secret"
    assert os.environ["CLOUDRU_PROJECT_ID"] == "cached-project"


def test_deploy_loader_rejects_world_readable_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _clear_cloudru_env(monkeypatch)
    state_file = tmp_path / "cloudru-codex.json"
    state_file.write_text("{}\n", encoding="utf-8")
    state_file.chmod(0o644)
    monkeypatch.setattr(cloudru_deploy, "CODEX_CLOUDRU_STATE_FILE", state_file)

    with pytest.raises(CloudProviderError, match="unsafe permissions"):
        cloudru_deploy._load_cloudru_credentials()


def test_deploy_loader_accepts_legacy_environment_names(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _clear_cloudru_env(monkeypatch)
    monkeypatch.setenv("CLOUDRU_KEY_ID", "legacy-id")
    monkeypatch.setenv("CLOUDRU_KEY_SECRET", "legacy-secret")
    monkeypatch.setattr(cloudru_deploy, "CODEX_CLOUDRU_STATE_FILE", tmp_path / "missing.json")

    cloudru_deploy._load_cloudru_credentials()

    assert os.environ["CLOUDRU_IAM_KEY_ID"] == "legacy-id"
    assert os.environ["CLOUDRU_IAM_KEY_SECRET"] == "legacy-secret"


def test_agent_credentials_are_materialized_without_tracing_secrets(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "gpg").write_text(
        "#!/bin/sh\ncat >/dev/null\nprintf '[GNUPG:] IMPORT_OK 1 TESTFINGERPRINT\\n'\n",
        encoding="utf-8",
    )
    (bin_dir / "ssh-keygen").write_text(
        "#!/bin/sh\ntest -f \"$3\"\nprintf 'ssh-ed25519 synthetic\\n'\n",
        encoding="utf-8",
    )
    (bin_dir / "gh").write_text(
        "#!/bin/sh\n"
        "if test \"$1 $2\" = 'auth login'; then\n"
        "  token=$(cat)\n"
        '  mkdir -p "$HOME/.config/gh"\n'
        '  printf \'oauth_token: %s\\n\' "$token" > "$HOME/.config/gh/hosts.yml"\n'
        "fi\n",
        encoding="utf-8",
    )
    for executable in bin_dir.iterdir():
        executable.chmod(0o700)

    env = os.environ.copy()
    for name in (
        "CODEX_GPG_PRIVATE_KEY",
        "GPG_PRIVATE_KEY",
        "CODEX_SSH_PRIVATE_KEY",
        "SSH_PRIVATE_KEY",
        "PREVIEW_SSH_PRIVATE_KEY",
        "CODEX_SSH_KNOWN_HOSTS",
        "SSH_KNOWN_HOSTS",
        "PREVIEW_SSH_KNOWN_HOSTS",
        "CLOUD_RU_SERVER_1",
        "CLOUD_RU_SERVER_1_pub",
        "CLOUD_RU_SERVER_2",
        "CLOUD_RU_SERVER_2_pub",
        "CODEX_GITHUB_TOKEN",
        "GITHUB_TOKEN",
    ):
        env.pop(name, None)
    env.update(
        {
            "HOME": str(tmp_path / "home"),
            "PATH": f"{bin_dir}:{env['PATH']}",
            "CODEX_GPG_PRIVATE_KEY": "gpg-secret-material",
            "CODEX_SSH_PRIVATE_KEY": "ssh-secret-material\\nsecond-line",
            "CLOUD_RU_SERVER_1": "ru.example.test",
            "CLOUD_RU_SERVER_1_pub": "ssh-ed25519 russian-host-key",
            "CLOUD_RU_SERVER_2": "us.example.test",
            "CLOUD_RU_SERVER_2_pub": "ssh-ed25519 american-host-key",
            "CODEX_GITHUB_TOKEN": "github-secret-token",
        }
    )
    result = subprocess.run(
        ["bash", "-c", f"set -euxo pipefail; source {CREDENTIAL_HELPER}"],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )

    home = Path(env["HOME"])
    private_key = home / ".ssh" / "id_ed25519"
    known_hosts = home / ".ssh" / "known_hosts"
    gh_hosts = home / ".config" / "gh" / "hosts.yml"
    assert private_key.read_text(encoding="utf-8") == "ssh-secret-material\nsecond-line\n"
    assert known_hosts.read_text(encoding="utf-8") == (
        "ru.example.test ssh-ed25519 russian-host-key\n"
        "us.example.test ssh-ed25519 american-host-key\n"
    )
    assert stat.S_IMODE(private_key.stat().st_mode) == 0o600
    assert stat.S_IMODE(known_hosts.stat().st_mode) == 0o600
    assert stat.S_IMODE(gh_hosts.stat().st_mode) == 0o600
    assert (
        subprocess.run(
            ["git", "config", "--global", "user.signingkey"],
            env=env,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        == "TESTFINGERPRINT"
    )
    output = result.stdout + result.stderr
    for secret in ("gpg-secret-material", "ssh-secret-material", "github-secret-token"):
        assert secret not in output
