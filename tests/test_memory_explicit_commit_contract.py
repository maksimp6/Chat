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


def test_two_workers_cannot_claim_same_task(tmp_path):
    """Only a durably committed claim counts as a successful claim."""
    import threading

    with MemoryStore(tmp_path / "tasks.memory") as db:
        db.set("tasks/1", {"status": "queued"})
        db.commit()
        claimed: list[int] = []
        errors: list[Exception] = []
        barrier = threading.Barrier(2)

        def worker(worker_id: int) -> None:
            try:
                barrier.wait(timeout=5)
                won = False
                with db.transaction() as tx:
                    task = tx.get("tasks/1")
                    if task["status"] == "queued":
                        tx.set("tasks/1", {"status": "running", "owner": worker_id})
                        won = True
                # The transaction context has exited: commit must have succeeded.
                if won:
                    claimed.append(worker_id)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        assert all(not thread.is_alive() for thread in threads)
        assert not errors
        assert len(claimed) == 1
        assert db.get("tasks/1")["owner"] == claimed[0]
