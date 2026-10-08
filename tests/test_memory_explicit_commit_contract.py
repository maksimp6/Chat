"""RED-first standalone Memory DB API: name/value staging and explicit commit.

These tests describe the accepted v1 public behavior. Existing namespaced,
auto-committing MemoryStore must fail until it is replaced.
"""

from memory_engine import MemoryStore


def test_set_is_staged_until_explicit_commit(tmp_path):
    path = tmp_path / "db.memory"
    with MemoryStore(path) as db:
        db.set("theme", "gray")
        assert db.get("theme") == "gray"
        assert db.last_commit.sequence == 0
        assert db.commit() == 1
    with MemoryStore(path) as reopened:
        assert reopened.get("theme") == "gray"


def test_multiple_sets_commit_atomically(tmp_path):
    path = tmp_path / "db.memory"
    with MemoryStore(path) as db:
        db.set("theme", "gray")
        db.set("theme", "dark")
        db.set("language", "ru")
        assert db.commit() == 1
        assert db.get("theme") == "dark"
        assert db.get("language") == "ru"
        assert db.commit() == 1


def test_uncommitted_changes_are_not_recovered(tmp_path):
    path = tmp_path / "db.memory"
    with MemoryStore(path) as db:
        db.set("saved", 1)
        db.commit()
        db.set("unsaved", 2)
    with MemoryStore(path) as reopened:
        assert reopened.get("saved") == 1
        assert reopened.get("unsaved") is None
