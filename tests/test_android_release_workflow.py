import os
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
RELEASE_WORKFLOW = WORKFLOWS / "release.yml"


def test_retired_packager_never_publishes_a_different_application(tmp_path) -> None:
    source = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    data = yaml.safe_load(source)
    assert data["permissions"] == {"contents": "read"}
    assert "secrets." not in source and "gh release create" not in source
    block = next(
        step for step in data["jobs"]["release"]["steps"] if step.get("id") == "policy_step_01"
    )
    result = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", block["run"]],
        cwd=tmp_path,
        env={**os.environ, "ALICE_RELEASE_STORE_PASSWORD": "SYNTHETIC_SIGNING_CANARY"},
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "No release will be published" in result.stdout
    assert "SYNTHETIC_SIGNING_CANARY" not in result.stdout + result.stderr
    assert not list(tmp_path.iterdir())


def test_job_level_env_does_not_use_step_only_contexts() -> None:
    # GitHub rejects the whole workflow when jobs.<id>.env references the runner context.
    for path in WORKFLOWS.glob("*.yml"):
        in_job_env = False
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            indent = len(line) - len(line.lstrip(" "))
            if line.strip() and indent <= 4:
                in_job_env = line == "    env:"
                continue
            if in_job_env and indent == 6:
                assert "runner." not in line, f"{path.name}:{number} uses runner context in job env"
