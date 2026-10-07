"""Architecture file-placement policy must reject new boundary violations."""

from pathlib import Path

import pytest

from scripts import check_architecture_paths as architecture


ROOT = Path(__file__).resolve().parents[1]


def test_forbidden_python_file_in_docs_fails():
    with pytest.raises(ValueError, match=r"ARCH_PATH_VIOLATION.*docs/runtime.py"):
        architecture.validate_changes([architecture.Change("A", "docs/runtime.py")])


def test_allowed_documentation_file_passes():
    architecture.validate_changes([architecture.Change("A", "docs/architecture/runtime.md")])


def test_rename_into_forbidden_area_fails():
    with pytest.raises(ValueError, match=r"ARCH_PATH_VIOLATION.*docs/runtime.py"):
        architecture.validate_changes(
            [architecture.Change("R", "docs/runtime.py", old_path="runtime.py")]
        )


def test_untouched_legacy_debt_is_not_scanned():
    architecture.validate_changes([])


def test_unknown_change_status_fails_closed():
    with pytest.raises(ValueError, match="ARCH_PATH_VIOLATION"):
        architecture.validate_changes([architecture.Change("?", "alice_platform/new.py")])


def test_architecture_gate_is_part_of_always_run_code_rules():
    script = (ROOT / "scripts/check_code_rules.sh").read_text(encoding="utf-8")
    assert "tests/test_architecture_paths.py" in script
