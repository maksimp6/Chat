"""RED-first requirements for replacing the SQL-backed task queue.

The replacement must retain FIFO claim, crash recovery and atomic event
recording; deleting SQLite imports without those behaviors is not acceptance.
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_task_store_uses_no_sql_engine() -> None:
    """A task store must depend on the Memory DB, not sqlite3."""
    source = (ROOT / "agent_shell/store.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert "sqlite3" not in imports
    assert "from memory_engine import MemoryStore" in source


def test_task_store_has_no_raw_sql_statements() -> None:
    """The replacement must use typed operations, never SQL strings."""
    source = (ROOT / "agent_shell/store.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    literals = [node.value.upper() for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)]
    assert not any("CREATE TABLE" in value or "BEGIN IMMEDIATE" in value for value in literals)
