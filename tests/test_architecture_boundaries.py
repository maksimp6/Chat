"""Fail-closed repository architecture boundaries.

Production/runtime sources must not depend on documentation or test code.
Tests may inspect production sources; contract tests may inspect docs/config.
"""

import ast
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]

TEST_FILE_PATTERNS = (".test.js", ".test.mjs", ".test.ts", ".spec.js", ".spec.mjs", ".spec.ts")
EXECUTABLE_SUFFIXES = {".py", ".js", ".mjs", ".ts", ".sh"}
EXCLUDED_TOP_LEVEL = {
    ".git",
    ".github",
    ".venv",
    "node_modules",
    "tests",
    "docs",
}


def _tracked_like_files(root: Path):
    """Return Git-tracked files for a checkout; synthetic fixtures use all files."""
    if (root / ".git").exists():
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            check=True,
            capture_output=True,
        )
        return [
            root / relative.decode("utf-8") for relative in result.stdout.split(b"\0") if relative
        ]
    return [path for path in root.rglob("*") if path.is_file()]


def _is_test_file(path: Path) -> bool:
    name = path.name
    return name.startswith("test_") and name.endswith(".py") or name.endswith(TEST_FILE_PATTERNS)


def _production_python(root: Path):
    for path in _tracked_like_files(root):
        relative = path.relative_to(root)
        if relative.parts[0] in EXCLUDED_TOP_LEVEL:
            continue
        if path.suffix == ".py":
            yield path


def _forbidden_python_imports(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    forbidden = []
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        for name in names:
            if (
                name == "tests"
                or name.startswith("tests.")
                or name == "docs"
                or name.startswith("docs.")
            ):
                forbidden.append(name)
    return forbidden


def architecture_violations(root: Path):
    violations = []
    for path in _tracked_like_files(root):
        relative = path.relative_to(root)
        if relative.parts[0] == "docs" and path.suffix in EXECUTABLE_SUFFIXES:
            violations.append(f"executable source under docs/: {relative.as_posix()}")
        if relative.parts[0] != "tests" and _is_test_file(path):
            violations.append(f"test file outside tests/: {relative.as_posix()}")

    for path in _production_python(root):
        for imported in _forbidden_python_imports(path):
            relative = path.relative_to(root).as_posix()
            violations.append(f"production import {relative} -> {imported}")
    return sorted(violations)


def test_repository_respects_architecture_boundaries():
    assert architecture_violations(ROOT) == []


def test_repository_guard_ignores_untracked_build_output(tmp_path):
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True)
    source = tmp_path / "alice.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "alice.py"], check=True)
    generated = tmp_path / "build" / "generated.test.mjs"
    generated.parent.mkdir()
    generated.write_text("export {};", encoding="utf-8")
    assert architecture_violations(tmp_path) == []


def test_guard_rejects_test_file_in_production_tree(tmp_path):
    path = tmp_path / "deploy" / "worker.test.mjs"
    path.parent.mkdir(parents=True)
    path.write_text("export {};", encoding="utf-8")
    assert architecture_violations(tmp_path) == ["test file outside tests/: deploy/worker.test.mjs"]


def test_guard_rejects_executable_source_under_docs(tmp_path):
    path = tmp_path / "docs" / "runtime.py"
    path.parent.mkdir(parents=True)
    path.write_text("print('runtime')\n", encoding="utf-8")
    assert architecture_violations(tmp_path) == ["executable source under docs/: docs/runtime.py"]


def test_guard_rejects_production_import_from_tests(tmp_path):
    path = tmp_path / "alice" / "runtime.py"
    path.parent.mkdir(parents=True)
    path.write_text("from tests.helpers import fake_client\n", encoding="utf-8")
    assert architecture_violations(tmp_path) == [
        "production import alice/runtime.py -> tests.helpers"
    ]


def test_guard_allows_tests_to_depend_on_production(tmp_path):
    production = tmp_path / "alice" / "runtime.py"
    production.parent.mkdir(parents=True)
    production.write_text("VALUE = 1\n", encoding="utf-8")
    test = tmp_path / "tests" / "test_runtime.py"
    test.parent.mkdir(parents=True)
    test.write_text("from alice.runtime import VALUE\n", encoding="utf-8")
    assert architecture_violations(tmp_path) == []
