import pytest

from memory_db import (
    Column,
    ColumnError,
    ConstraintError,
    MemoryDatabase,
    TableNotFound,
    TransactionError,
)


def make_db():
    db = MemoryDatabase()
    db.create_table(
        "users",
        [
            Column("id", int, nullable=False, unique=True),
            Column("name", str, nullable=False),
            Column("email", str, nullable=False, unique=True),
            Column("active", bool, nullable=False, default=True),
        ],
    )
    return db


def test_crud_and_defaults():
    db = make_db()
    row = db.insert("users", id=1, name="Алиса", email="a@example.test")
    assert row["active"] is True
    assert db.select("users", lambda r: r["id"] == 1)[0]["name"] == "Алиса"
    assert db.update("users", lambda r: r["id"] == 1, name="Alice") == 1
    assert db.select("users")[0]["name"] == "Alice"
    assert db.delete("users", lambda r: r["id"] == 1) == 1
    assert db.select("users") == []


def test_constraints_and_unknown_columns():
    db = make_db()
    db.insert("users", id=1, name="A", email="a@example.test")
    with pytest.raises(ConstraintError):
        db.insert("users", id=1, name="B", email="b@example.test")
    with pytest.raises(ConstraintError):
        db.insert("users", id=2, name="B", email="a@example.test")
    with pytest.raises(ConstraintError):
        db.insert("users", id=2, name=None, email="b@example.test")
    with pytest.raises(ColumnError):
        db.insert("users", id="2", name="B", email="b@example.test")
    with pytest.raises(ColumnError):
        db.insert("users", id=2, name="B", email="b@example.test", extra=1)


def test_missing_table():
    db = MemoryDatabase()
    with pytest.raises(TableNotFound):
        db.select("missing")


def test_transaction_commit_and_rollback():
    db = make_db()
    db.insert("users", id=1, name="A", email="a@example.test")
    with db.transaction():
        db.insert("users", id=2, name="B", email="b@example.test")
    assert len(db.select("users")) == 2

    with pytest.raises(RuntimeError):
        with db.transaction():
            db.insert("users", id=3, name="C", email="c@example.test")
            raise RuntimeError("rollback")
    assert [r["id"] for r in db.select("users")] == [1, 2]


def test_explicit_transaction_state_errors():
    db = make_db()
    tx = db.transaction()
    with pytest.raises(TransactionError):
        tx.commit()
    with tx:
        db.insert("users", id=1, name="A", email="a@example.test")
    with pytest.raises(TransactionError):
        tx.rollback()


def test_table_duplicate_and_drop():
    db = make_db()
    with pytest.raises(ColumnError):
        db.create_table("bad", [Column("id"), Column("id")])
    with pytest.raises(Exception):
        db.create_table("users", [Column("id")])
    db.drop_table("users")
    with pytest.raises(TableNotFound):
        db.table("users")


def test_isolation_from_returned_rows():
    db = make_db()
    row = db.insert("users", id=1, name="A", email="a@example.test")
    row["name"] = "MUTATED"
    assert db.select("users")[0]["name"] == "A"
    selected = db.select("users")
    selected[0]["name"] = "MUTATED"
    assert db.select("users")[0]["name"] == "A"


def test_clear():
    db = make_db()
    db.insert("users", id=1, name="A", email="a@example.test")
    db.clear()
    assert db.select("users") == []
