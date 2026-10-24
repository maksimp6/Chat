from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FORMAT_SCRIPT = ROOT / "scripts" / "format.sh"
REQUIREMENTS_DEV = ROOT / "requirements-dev.txt"


def test_formatter_is_ruff_only_and_pinned() -> None:
    requirements = REQUIREMENTS_DEV.read_text(encoding="utf-8")
    format_script = FORMAT_SCRIPT.read_text(encoding="utf-8")

    assert "ruff==" in requirements
    assert "ruff format" in format_script
    assert "prettier" not in format_script
    assert "npx" not in format_script
    assert "node_modules" not in format_script
    assert not (ROOT / "package.json").exists()
