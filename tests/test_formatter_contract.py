from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FORMAT_SCRIPT = ROOT / "scripts" / "format.sh"
FORMAT_WORKFLOW = ROOT / ".github" / "workflows" / "format.yml"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
REQUIREMENTS_DEV = ROOT / "requirements-dev.txt"
PACKAGE_JSON = ROOT / "package.json"


def test_formatter_versions_have_one_repository_source_of_truth() -> None:
    requirements = REQUIREMENTS_DEV.read_text(encoding="utf-8")
    package_json = PACKAGE_JSON.read_text(encoding="utf-8")
    format_script = FORMAT_SCRIPT.read_text(encoding="utf-8")

    assert "ruff==" in requirements
    assert '"prettier":' in package_json

    assert "ALICE_PRETTIER_VERSION" not in format_script
    assert "npx" not in format_script
    assert "command -v prettier" not in format_script
    assert "prettier --version" not in format_script


def test_format_and_application_ci_use_same_pinned_toolchain_and_entrypoint() -> None:
    format_workflow = FORMAT_WORKFLOW.read_text(encoding="utf-8")
    ci_workflow = CI_WORKFLOW.read_text(encoding="utf-8")

    assert "pip install -r requirements-dev.txt" in format_workflow
    assert (
        "npm install --ignore-scripts --no-audit --no-fund --package-lock=false" in format_workflow
    )
    assert "pip install --upgrade ruff" not in format_workflow

    assert "pip install -r requirements-dev.txt" in ci_workflow
    assert "npm install --ignore-scripts --no-audit --no-fund --package-lock=false" in ci_workflow

    assert "bash scripts/format.sh write" in format_workflow
    assert "bash scripts/format.sh check" in ci_workflow
