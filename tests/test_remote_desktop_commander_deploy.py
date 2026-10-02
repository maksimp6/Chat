"""Exercise the deploy shell with a fake Docker CLI; no server or real credentials."""

import io
import json
import os
from pathlib import Path
import subprocess
import tarfile

import pytest
import yaml


DEPLOY = Path(__file__).resolve().parents[1] / "deploy/remote-desktop-commander"


def harness(tmp_path):
    home = tmp_path / "server-home"
    home.mkdir()
    binary = tmp_path / "bin"
    binary.mkdir()
    log = tmp_path / "docker.log"
    docker = binary / "docker"
    docker.write_text(
        '#!/bin/bash\nprintf "%s\\n" "$*" >> "$DOCKER_TEST_LOG"\nexit "${DOCKER_TEST_EXIT:-0}"\n'
    )
    docker.chmod(0o755)
    env = {
        **os.environ,
        "ALICE_RDC_ROOT": str(home / "alice-preview/services/remote-desktop-commander"),
        "ALICE_RDC_ARCHIVE": str(home / "alice-preview/incoming/remote-desktop-commander.tar.gz"),
        "PATH": str(binary) + os.pathsep + os.environ["PATH"],
        "DOCKER_TEST_LOG": str(log),
    }
    return home, log, env


def archive(home):
    incoming = home / "alice-preview/incoming"
    incoming.mkdir(parents=True)
    with tarfile.open(incoming / "remote-desktop-commander.tar.gz", "w:gz") as tar:
        for path in DEPLOY.iterdir():
            if path.is_file():
                data = path.read_bytes()
                info = tarfile.TarInfo("deploy/remote-desktop-commander/" + path.name)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))


def run(env, *args):
    return subprocess.run(
        ["bash", str(DEPLOY / "server.sh"), *args], env=env, capture_output=True, text=True
    )


def test_install_retries_use_clean_context_and_preserve_state(tmp_path):
    home, log, env = harness(tmp_path)
    archive(home)
    revision = "a" * 40
    root = home / "alice-preview/services/remote-desktop-commander"
    assert run(env, "install", revision).returncode == 0
    (root / "state/auth-sentinel").write_text("not-a-real-secret")
    (root / "workspace/work.txt").write_text("keep")
    first = list((root / "releases").iterdir())
    (first[0] / "stale.txt").write_text("old file")
    assert run(env, "install", revision).returncode == 0
    releases = list((root / "releases").iterdir())
    assert len(releases) == 2
    assert sum((item / "stale.txt").exists() for item in releases) == 1
    assert (root / "state/auth-sentinel").read_text() == "not-a-real-secret"
    assert (root / "workspace/work.txt").read_text() == "keep"
    commands = log.read_text()
    assert (root / ".dockerignore").read_text().splitlines()[:3] == [
        "state/",
        "workspace/",
        "releases/",
    ]
    assert "compose config --quiet" in commands
    assert "compose run --rm --no-deps initialize" in commands
    assert "compose up -d --no-build" in commands
    assert "compose exec -T commander node" in commands
    assert "logs" not in commands
    assert "/var/run/docker.sock" not in commands


@pytest.mark.parametrize("operation", ["preflight", "install"])
def test_docker_failure_is_reported(tmp_path, operation):
    home, _, env = harness(tmp_path)
    archive(home)
    env["DOCKER_TEST_EXIT"] = "19"
    assert run(env, operation, "a" * 40).returncode == 19


def test_invalid_revision_and_operation_do_not_invoke_docker(tmp_path):
    _, log, env = harness(tmp_path)
    assert run(env, "install", "master; echo unsafe").returncode != 0
    assert run(env, "destroy").returncode != 0
    assert not log.exists()


def test_compose_isolated_mounts_and_no_host_control():
    config = yaml.safe_load((DEPLOY / "compose.yaml").read_text())
    service = config["services"]["commander"]
    assert service["volumes"] == ["./state:/home/node", "./workspace:/workspace"]
    assert service["user"] == "1000:1000"
    assert service["read_only"] is True
    assert service["cap_drop"] == ["ALL"]
    assert service["security_opt"] == ["no-new-privileges:true"]
    assert not service.get("ports")
    assert not service.get("privileged")
    assert not service.get("network_mode")
    assert service["restart"] == "unless-stopped"
    initializer = config["services"]["initialize"]
    assert initializer["profiles"] == ["setup"]
    assert initializer["network_mode"] == "none"
    assert initializer["cap_drop"] == ["ALL"]
    assert initializer["cap_add"] == ["CHOWN", "FOWNER"]
    assert initializer["volumes"] == service["volumes"]
    assert "cap_add" not in service
    defaults = json.loads((DEPLOY / "config.json").read_text())
    assert defaults["allowedDirectories"] == ["/workspace"]
    assert defaults["telemetryEnabled"] is False


def test_workflow_master_only_and_no_pairing_logs():
    workflow = yaml.safe_load(
        (DEPLOY.parents[1] / ".github/workflows/remote-desktop-commander.yml").read_text()
    )
    job = workflow["jobs"]["deploy"]
    assert job["environment"] == "production"
    gate = job["if"]
    assert "github.ref == 'refs/heads/master'" in gate
    assert "github.event_name == 'workflow_dispatch'" in gate
    assert "github.event.issue.number == 409" in gate
    assert "github.event.comment.author_association == 'OWNER'" in gate
    assert "github.event.comment.user.login == github.repository_owner" in gate
    assert (
        json.dumps(["/rdc preflight", "/rdc install", "/rdc status"], separators=(",", ":")) in gate
    )
    assert job["steps"][0]["with"]["ref"] == "${{ github.sha }}"
    assert "github.event.comment.body" not in "\n".join(
        step.get("run", "") for step in job["steps"]
    )
    assert workflow["permissions"] == {"contents": "read"}
    scripts = "\n".join(step.get("run", "") for step in job["steps"])
    assert "StrictHostKeyChecking=yes" in scripts
    assert "docker compose logs" not in scripts
    assert "device.json" not in scripts
    assert "${{ inputs." not in scripts
    assert "${{ secrets." not in scripts


def test_image_uses_frozen_dependency_graph():
    package = json.loads((DEPLOY / "package.json").read_text())
    lock = json.loads((DEPLOY / "package-lock.json").read_text())
    assert package["dependencies"] == {"@wonderwhy-er/desktop-commander": "0.2.52"}
    assert package["overrides"] == {"sharp": "0.35.4", "exceljs": {"uuid": "11.1.1"}}
    assert lock["packages"][""]["dependencies"] == package["dependencies"]
    assert lock["packages"]["node_modules/@wonderwhy-er/desktop-commander"]["version"] == "0.2.52"
    dockerfile = (DEPLOY / "Dockerfile").read_text()
    assert "npm ci --omit=dev --ignore-scripts" in dockerfile
    assert "npm install" not in dockerfile
