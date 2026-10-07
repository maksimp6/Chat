from __future__ import annotations

from pathlib import Path

from scripts.check_runbook_refs import validate_runbook_references


def test_accepts_existing_script_workflow_and_python_module(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "scripts").mkdir()
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / "pkg").mkdir()
    (tmp_path / "scripts" / "tool.py").write_text("", encoding="utf-8")
    (tmp_path / ".github" / "workflows" / "deploy.yml").write_text("", encoding="utf-8")
    (tmp_path / "pkg" / "__main__.py").write_text("", encoding="utf-8")
    source = tmp_path / "docs" / "runbook.md"
    source.write_text(
        "Run `scripts/tool.py`, `.github/workflows/deploy.yml`, "
        "and `python -m pkg validate`.\n",
        encoding="utf-8",
    )
    assert validate_runbook_references(tmp_path, [source]) == []


def test_reports_missing_repository_references(tmp_path):
    (tmp_path / "docs").mkdir()
    source = tmp_path / "docs" / "runbook.md"
    source.write_text(
        "`scripts/missing.sh` `.github/workflows/nope.yml` "
        "`python -m absent deploy`\n",
        encoding="utf-8",
    )
    errors = validate_runbook_references(tmp_path, [source])
    assert errors == [
        "docs/runbook.md: missing repository path: scripts/missing.sh",
        "docs/runbook.md: missing repository path: .github/workflows/nope.yml",
        "docs/runbook.md: missing Python module: absent",
    ]


def test_ignores_external_commands_and_non_command_prose(tmp_path):
    (tmp_path / "docs").mkdir()
    source = tmp_path / "docs" / "runbook.md"
    source.write_text(
        "`git status` `docker ps` `curl https://example.com` "
        "`config/alice/platform.yaml`\n",
        encoding="utf-8",
    )
    assert validate_runbook_references(tmp_path, [source]) == []
