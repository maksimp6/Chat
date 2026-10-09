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
            name == "db"
            or name.startswith(("agent_shell", "tool_providers", "flask", "mcp_storage"))
            for name in imported
        ), source_file.name


def test_engine_has_no_global_database_provider():
    """Clients create/own instances; no process-global database owner in core."""
    source = (ROOT / "store.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden = {"bind_database_info", "database_info", "_database_info_provider"}
    definitions = {
        node.name
        for node in ast.walk(tree)
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


def test_public_store_does_not_override_incompatible_legacy_api():
    """A name/value store must not inherit namespace/key method signatures."""
    source = (ROOT / "store.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    public = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "MemoryStore"
    )
    inherited = {base.id for base in public.bases if isinstance(base, ast.Name)}
    assert "_JournalEngine" not in inherited, (
        "Public MemoryStore must not inherit journal internals; "
        "use composition or a compatible storage engine"
    )


def test_public_contract_requires_annotated_parameters_and_returns():
    """Enforce typing at API boundaries, not on inferred local variables."""
    source = (ROOT / "store.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    public = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "MemoryStore"
    )
    methods = {
        node.name: node
        for node in public.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for name in ("get", "set", "commit"):
        method = methods[name]
        assert method.returns is not None, name
        assert all(arg.annotation is not None for arg in method.args.args[1:]), name



def test_legacy_namespaced_transaction_api_is_removed():
    """RED: v1 must not retain a second namespaced transaction implementation."""
    tree = ast.parse((ROOT / "store.py").read_text(encoding="utf-8"))
    classes = {
        node.name: node for node in tree.body if isinstance(node, ast.ClassDef)
    }
    assert "_JournalTransaction" not in classes, (
        "Remove the redundant legacy transaction class after migrating its tests"
    )
    engine = classes["_JournalEngine"]
    methods = {
        node.name for node in engine.body if isinstance(node, ast.FunctionDef)
    }
    assert "transaction" not in methods, "Use the single value_transaction owner"
    assert "delete" not in methods, "Remove legacy namespaced delete after migration"
