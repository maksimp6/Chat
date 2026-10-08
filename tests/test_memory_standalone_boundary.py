"""Standalone-library architecture guards, independent of Alice runtime."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "memory_engine"


def test_engine_does_not_import_alice_modules():
    """The reusable library may use the standard library, never Alice internals."""
    for source_file in ROOT.glob("*.py"):
        tree = ast.parse(source_file.read_text(encoding="utf-8"))
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
        assert not any(
            name == "db" or name.startswith(("agent_shell", "tool_providers", "flask", "mcp_storage"))
            for name in imported
        ), source_file.name


def test_engine_has_no_global_database_provider():
    """Clients create/own instances; no process-global database owner in core."""
    source = (ROOT / "store.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden = {"bind_database_info", "database_info", "_database_info_provider"}
    definitions = {
        node.name for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assignments = {
        target.id
        for node in ast.walk(tree)
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
        if isinstance(target, ast.Name)
    }
    assert not (forbidden & (definitions | assignments))
