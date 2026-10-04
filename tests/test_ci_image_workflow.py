from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ci-image.yml"


def test_ci_image_builder_has_minimal_write_permissions() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "contents: read" in workflow
    assert "packages: write" in workflow
    assert "contents: write" not in workflow
    assert "pull-requests: write" not in workflow
    assert "issues: write" not in workflow


def test_ci_image_rebuilds_only_for_environment_inputs() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    for path in (
        "deploy/ci/Dockerfile",
        "requirements.txt",
        "requirements-dev.txt",
        "package.json",
        "scripts/ci_environment_hash.py",
    ):
        assert f"- {path}" in workflow

    assert "cache-from: type=gha,scope=alice-ci-image" in workflow
    assert "cache-to: type=gha,mode=max,scope=alice-ci-image" in workflow
    assert "ghcr.io/maksimp6/chat/alice-ci:" in workflow
