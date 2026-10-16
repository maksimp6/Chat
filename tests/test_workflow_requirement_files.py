"""Every requirements file named by a workflow `pip install -r` must exist.

Issue #1191: cloudru-openai-tunnel.yml still installed requirements-postgres.txt,
which was removed with the PostgreSQL backend, so dispatching it from develop failed.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
PIP_REQUIREMENT = re.compile(r"pip install\b[^\n]*")
REQUIREMENT_FILE = re.compile(r"-r\s+(\S+)")


def _requirement_files() -> list[tuple[str, str]]:
    found = []
    for workflow in WORKFLOWS:
        for command in PIP_REQUIREMENT.findall(workflow.read_text(encoding="utf-8")):
            for name in REQUIREMENT_FILE.findall(command):
                found.append((workflow.name, name))
    return found


def test_workflows_reference_requirement_files():
    assert _requirement_files(), "expected pip install -r lines in workflows"


@pytest.mark.parametrize(("workflow", "requirement"), _requirement_files())
def test_workflow_requirement_file_exists(workflow, requirement):
    assert (ROOT / requirement).is_file(), f"{workflow} installs missing {requirement}"


def test_no_workflow_installs_retired_postgres_requirements():
    names = {name for _, name in _requirement_files()}
    assert "requirements-postgres.txt" not in names
