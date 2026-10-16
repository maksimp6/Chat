"""The 3D-printing domain must not depend on Alice Pro host modules."""

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOMAIN = ROOT / "printing3d"
ALLOWED_ROOTS = set(sys.stdlib_module_names) | {"printing3d", "__future__"}


def _imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


def test_domain_imports_only_stdlib_and_itself():
    offenders = {
        path.relative_to(ROOT).as_posix(): sorted(_imported_roots(path) - ALLOWED_ROOTS)
        for path in DOMAIN.rglob("*.py")
        if _imported_roots(path) - ALLOWED_ROOTS
    }
    assert not offenders, f"printing3d must stay standalone: {offenders}"


def test_host_binding_lives_outside_the_domain():
    assert (ROOT / "forge" / "binding.py").exists()
    assert not (DOMAIN / "routes.py").exists()
    assert not (DOMAIN / "tools.py").exists()
