"""RED-first Alice module build, file manifest and import-boundary checks."""

import ast
import os
from pathlib import Path
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "alice_pro"
MAX_FILE_BYTES = 4096
MAX_FILE_LINES = 80


def test_contract_test_file_stays_small():
    """Keep this contract readable in full without splitting responsibilities."""
    source = Path(__file__).read_bytes()
    assert len(source) <= MAX_FILE_BYTES
    assert len(source.splitlines()) <= MAX_FILE_LINES


def test_package_layout_and_metadata():
    assert (PACKAGE / "__init__.py").is_file()
    assert (PACKAGE / "__main__.py").is_file()
    metadata = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "[build-system]" in metadata and "[project]" in metadata


def test_entrypoint_is_guarded():
    tree = ast.parse((PACKAGE / "__main__.py").read_text(encoding="utf-8"))
    assert any(
        isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and isinstance(node.test.left, ast.Name)
        and node.test.left.id == "__name__"
        for node in tree.body
    )


def test_wheel_manifest(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "pip", "wheel", "--no-deps",
         "--no-build-isolation", "--wheel-dir", str(tmp_path), str(ROOT)],
        capture_output=True, text=True, timeout=45, check=False,
    )
    assert result.returncode == 0, result.stderr
    wheels = list(tmp_path.glob("*.whl"))
    assert len(wheels) == 1
    with zipfile.ZipFile(wheels[0]) as wheel:
        names = set(wheel.namelist())
    assert {"alice_pro/__init__.py", "alice_pro/__main__.py"} <= names
    assert any(n.endswith(".dist-info/METADATA") for n in names)
    assert any(n.endswith(".dist-info/entry_points.txt") for n in names)
    assert not any(n.endswith((".db", ".sqlite", ".sqlite3", ".env", ".pem", ".key"))
                   for n in names)


def test_module_help_outside_repo(tmp_path):
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    result = subprocess.run(
        [sys.executable, "-m", "alice_pro", "--help"],
        cwd=tmp_path, env=env, capture_output=True, text=True,
        timeout=15, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip()


def test_no_private_memory_imports():
    assert PACKAGE.is_dir()
    for source in PACKAGE.rglob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        assert all(
            node.module != "memory_engine.store"
            for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
        )
