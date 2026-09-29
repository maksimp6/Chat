import pytest

from tests.postgres_test_guard import require_disposable_postgres_target


def test_postgres_reset_requires_explicit_opt_in(monkeypatch):
    monkeypatch.delenv("ALICE_PYTEST_POSTGRES_RESET", raising=False)

    with pytest.raises(RuntimeError, match="ALICE_PYTEST_POSTGRES_RESET=1"):
        require_disposable_postgres_target(
            "postgresql://alice:secret@localhost:5432/alice_pro_test"
        )


def test_postgres_reset_rejects_non_test_database(monkeypatch):
    monkeypatch.setenv("ALICE_PYTEST_POSTGRES_RESET", "1")

    with pytest.raises(RuntimeError, match="non-test database"):
        require_disposable_postgres_target("postgresql://alice:secret@db.example:5432/alice_pro")


@pytest.mark.parametrize("database_name", ["alice_pro_test", "alice_pro_ci"])
def test_postgres_reset_accepts_explicit_disposable_database(monkeypatch, database_name):
    monkeypatch.setenv("ALICE_PYTEST_POSTGRES_RESET", "1")

    require_disposable_postgres_target(f"postgresql://alice:secret@localhost:5432/{database_name}")
