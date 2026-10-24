from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FORMAT_SCRIPT = ROOT / "scripts" / "format.sh"
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
