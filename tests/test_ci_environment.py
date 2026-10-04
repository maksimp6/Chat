from pathlib import Path

from scripts.ci_environment_hash import INPUTS


ROOT = Path(__file__).resolve().parents[1]


def test_ci_environment_hash_covers_toolchain_inputs() -> None:
    assert INPUTS == (
        "deploy/ci/Dockerfile",
        "requirements.txt",
        "requirements-dev.txt",
        "package.json",
    )
    for relative in INPUTS:
        assert (ROOT / relative).is_file()


def test_ci_environment_image_contains_shared_python_and_node_toolchains() -> None:
    dockerfile = (ROOT / "deploy" / "ci" / "Dockerfile").read_text(encoding="utf-8")

    assert "FROM python:3.14-slim" in dockerfile
    assert "requirements.txt requirements-dev.txt" in dockerfile
    assert "pip install -r requirements.txt -r requirements-dev.txt" in dockerfile
    assert "COPY package.json" in dockerfile
    assert "npm install --ignore-scripts --no-audit --no-fund --package-lock=false" in dockerfile
