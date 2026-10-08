"""Memory DB inspection tool contract: no SQL execution or mutations."""

from pathlib import Path

from memory_engine import MemoryStore
from tool_providers.filesystem import memory_inspect


def test_memory_inspect_reads_committed_values(tmp_path, monkeypatch):
    import tool_providers.filesystem as filesystem

    monkeypatch.setattr(filesystem, "BASE_DIR", str(tmp_path))
    path = Path(tmp_path) / "alice.memory"
    with MemoryStore(path) as db:
        db.set("settings", "theme", "gray")
        db.set("settings", "language", "ru")
        db.set("private", "secret", "not-exposed-in-settings")
    result = memory_inspect({"db_path": "alice.memory", "namespace": "settings", "limit": 1})
    assert result["success"] is True
    assert result["sequence"] == 3
    assert result["count"] == 1
    assert result["rows"] == [{"key": "language", "value": "ru"}]
    assert "private" not in result


def test_memory_inspect_rejects_invalid_namespace_and_limit():
    assert "error" in memory_inspect({})
    assert "error" in memory_inspect({"namespace": "settings", "limit": 0})


def test_memory_inspect_fails_closed_on_corruption(tmp_path, monkeypatch):
    import tool_providers.filesystem as filesystem

    monkeypatch.setattr(filesystem, "BASE_DIR", str(tmp_path))
    path = Path(tmp_path) / "alice.memory"
    with MemoryStore(path) as db:
        db.set("settings", "theme", "gray")
    with path.open("ab") as output:
        output.write(b"unfinished")
    result = memory_inspect({"db_path": "alice.memory", "namespace": "settings"})
    assert "error" in result
    assert "rows" not in result
