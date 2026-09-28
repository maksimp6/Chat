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
