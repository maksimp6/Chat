"""RED-first checks for Alice's installable module and wheel contents.

The package name `alice_pro` is the intended canonical module for #1050.
These tests deliberately fail until packaging and module entrypoints exist.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODULE = "alice_pro"


def test_package_files_and_metadata_exist() -> None:
    """An installable module needs a package and explicit build metadata."""
    assert (ROOT / MODULE / "__init__.py").is_file()
    assert (ROOT / MODULE / "__main__.py").is_file()
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "[build-system]" in text
    assert "[project]" in text


def test_entrypoint_has_main_guard() -> None:
    """Importing the package must not launch Alice as a side effect."""
    tree = ast.parse((ROOT / MODULE / "__main__.py").read_text(encoding="utf-8"))
    assert any(
        isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and isinstance(node.test.left, ast.Name)
        and node.test.left.id == "__name__"
        for node in tree.body
    )


def test_wheel_contains_module_and_entrypoint(tmp_path: Path) -> None:
    """Build the actual wheel and inspect its file list, not just exit status."""
    build = subprocess.run(
        [sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation",
         "--wheel-dir", str(tmp_path), str(ROOT)],
        capture_output=True, text=True, timeout=45, check=False,
    )
    assert build.returncode == 0, build.stderr
    wheels = list(tmp_path.glob("*.whl"))
    assert len(wheels) == 1
    with zipfile.ZipFile(wheels[0]) as wheel:
        files = set(wheel.namelist())
        assert f"{MODULE}/__init__.py" in files
        assert f"{MODULE}/__main__.py" in files
        assert any(name.endswith(".dist-info/METADATA") for name in files)
        assert any(name.endswith(".dist-info/entry_points.txt") for name in files)
        assert not any(
            name.endswith((".db", ".sqlite", ".sqlite3", ".env", ".pem", ".key"))
            for name in files
        )


def test_module_launches_outside_repo(tmp_path: Path) -> None:
    """Installed Alice must not depend on the repository working directory."""
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    result = subprocess.run(
        [sys.executable, "-m", MODULE, "--help"],
        cwd=tmp_path, env=env, capture_output=True, text=True,
        timeout=15, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip()


def test_package_source_does_not_import_private_memory_engine(tmp_path: Path) -> None:
    """Alice should use the public Memory DB API, not storage internals."""
    package = ROOT / MODULE
    assert package.is_dir()
    for file in package.rglob("*.py"):
        tree = ast.parse(file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert node.module != "memory_engine.store", str(file)
