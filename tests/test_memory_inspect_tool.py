"""Memory DB inspector never discloses data or races a live writer."""

from pathlib import Path

from memory_engine import MemoryStore
from tool_providers.filesystem import memory_inspect


def test_inspector_returns_metadata_only(tmp_path, monkeypatch):
    import db

    path = Path(tmp_path) / "alice.memory"
    monkeypatch.setattr(db, "memory_file_path", lambda: path)
    with MemoryStore(path) as store:
        store.set("secrets", "token", "PRIVATE_TOKEN")
        assert "error" in memory_inspect({})
    result = memory_inspect({})
    assert result["success"] is True
    assert result["sequence"] == 1
    assert isinstance(result["digest"], str)
    assert "PRIVATE_TOKEN" not in str(result)
    assert "token" not in str(result)
    assert "rows" not in result


def test_inspector_rejects_arbitrary_paths_and_namespace(tmp_path, monkeypatch):
    import db

    path = Path(tmp_path) / "alice.memory"
    monkeypatch.setattr(db, "memory_file_path", lambda: path)
    for arguments in ({"namespace": "secrets"}, {"db_path": "../private"}, {"limit": 1}):
        assert "error" in memory_inspect(arguments)


def test_inspector_fails_closed_on_corruption(tmp_path, monkeypatch):
    import db

    path = Path(tmp_path) / "alice.memory"
    monkeypatch.setattr(db, "memory_file_path", lambda: path)
    with MemoryStore(path) as store:
        store.set("settings", "theme", "gray")
    with path.open("ab") as output:
        output.write(b"unfinished")
    result = memory_inspect({})
    assert "error" in result
    assert "rows" not in result


def test_inspector_rejects_large_journal(tmp_path, monkeypatch):
    import db

    path = Path(tmp_path) / "alice.memory"
    monkeypatch.setattr(db, "memory_file_path", lambda: path)
    with MemoryStore(path):
        pass
    with path.open("wb") as output:
        output.truncate(8 * 1024 * 1024 + 1)
    assert "error" in memory_inspect({})
