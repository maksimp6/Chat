"""Small process-local database engine used for deterministic tests and local development.

It intentionally does not emulate SQL. The application talks to a typed repository-like
API, while this engine owns tables, constraints and transactions in memory.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from threading import RLock
from typing import Any, Callable, Iterable


class DatabaseError(Exception):
    """Base error for the in-memory database."""


class TableNotFound(DatabaseError):
    pass


class ColumnError(DatabaseError):
    pass


class ConstraintError(DatabaseError):
    pass


class TransactionError(DatabaseError):
    pass


@dataclass(frozen=True)
class Column:
    name: str
    kind: type | tuple[type, ...] = object
    nullable: bool = True
    unique: bool = False
    default: Any = None


class Table:
    def __init__(self, name: str, columns: Iterable[Column]) -> None:
        self.name = name
        self.columns = tuple(columns)
        self._column_map = {column.name: column for column in self.columns}
        if len(self._column_map) != len(self.columns):
            raise ColumnError(f"duplicate column in {name!r}")
        self.rows: list[dict[str, Any]] = []

    def _validate(
        self, values: dict[str, Any], existing: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        unknown = set(values) - set(self._column_map)
        if unknown:
            raise ColumnError(f"unknown columns: {sorted(unknown)}")

        row = {
            column.name: (
                values[column.name] if column.name in values else deepcopy(column.default)
            )
            for column in self.columns
        }

        for column in self.columns:
            value = row[column.name]
            if value is None:
                if not column.nullable:
                    raise ConstraintError(f"{self.name}.{column.name} cannot be NULL")
                continue
            if column.kind is not object and not isinstance(value, column.kind):
                raise ColumnError(
                    f"{self.name}.{column.name} expects {column.kind}, got {type(value)}"
                )

        for column in self.columns:
            if not column.unique or row[column.name] is None:
                continue
            for old in self.rows:
                if old is not existing and old[column.name] == row[column.name]:
                    raise ConstraintError(f"unique constraint failed: {self.name}.{column.name}")
        return row

    def insert(self, values: dict[str, Any]) -> dict[str, Any]:
        row = self._validate(values)
        self.rows.append(row)
        return deepcopy(row)

    def update(
        self,
        predicate: Callable[[dict[str, Any]], bool],
        values: dict[str, Any],
    ) -> int:
        changed = 0
        for old in list(self.rows):
            if predicate(deepcopy(old)):
                new = dict(old)
                new.update(values)
                validated = self._validate(new, existing=old)
                old.clear()
                old.update(validated)
                changed += 1
        return changed

    def delete(self, predicate: Callable[[dict[str, Any]], bool]) -> int:
        before = len(self.rows)
        self.rows[:] = [row for row in self.rows if not predicate(deepcopy(row))]
        return before - len(self.rows)

    def select(
        self,
        predicate: Callable[[dict[str, Any]], bool] | None = None,
    ) -> list[dict[str, Any]]:
        predicate = predicate or (lambda _row: True)
        return [deepcopy(row) for row in self.rows if predicate(deepcopy(row))]


class MemoryDatabase:
    """Thread-safe in-memory database with snapshot transactions."""

    def __init__(self) -> None:
        self._tables: dict[str, Table] = {}
        self._lock = RLock()
        self._snapshots: list[dict[str, Table]] = []

    def create_table(self, name: str, columns: Iterable[Column]) -> Table:
        with self._lock:
            if name in self._tables:
                raise DatabaseError(f"table already exists: {name}")
            table = Table(name, columns)
            self._tables[name] = table
            return table

    def drop_table(self, name: str) -> None:
        with self._lock:
            self._require_table(name)
            del self._tables[name]

    def table(self, name: str) -> Table:
        with self._lock:
            return self._require_table(name)

    def insert(self, table: str, **values: Any) -> dict[str, Any]:
        with self._lock:
            return self._require_table(table).insert(values)

    def select(
        self,
        table: str,
        predicate: Callable[[dict[str, Any]], bool] | None = None,
    ) -> list[dict[str, Any]]:
        with self._lock:
            return self._require_table(table).select(predicate)

    def update(
        self,
        table: str,
        predicate: Callable[[dict[str, Any]], bool],
        **values: Any,
    ) -> int:
        with self._lock:
            return self._require_table(table).update(predicate, values)

    def delete(
        self,
        table: str,
        predicate: Callable[[dict[str, Any]], bool],
    ) -> int:
        with self._lock:
            return self._require_table(table).delete(predicate)

    def transaction(self) -> "_Transaction":
        return _Transaction(self)

    def clear(self) -> None:
        with self._lock:
            for table in self._tables.values():
                table.rows.clear()

    def reset(self) -> None:
        with self._lock:
            self._tables.clear()
            self._snapshots.clear()

    def _require_table(self, name: str) -> Table:
        try:
            return self._tables[name]
        except KeyError as exc:
            raise TableNotFound(name) from exc


class _Transaction:
    def __init__(self, db: MemoryDatabase) -> None:
        self.db = db
        self._active = False

    def __enter__(self) -> MemoryDatabase:
        with self.db._lock:
            if self._active:
                raise TransactionError("transaction already active")
            self.db._snapshots.append(deepcopy(self.db._tables))
            self._active = True
        return self.db

    def commit(self) -> None:
        with self.db._lock:
            if not self._active:
                raise TransactionError("transaction is not active")
            self.db._snapshots.pop()
            self._active = False

    def rollback(self) -> None:
        with self.db._lock:
            if not self._active:
                raise TransactionError("transaction is not active")
            snapshot = self.db._snapshots.pop()
            self.db._tables = snapshot
            self._active = False

    def __exit__(self, exc_type, exc, tb) -> None:
        if not self._active:
            return
        if exc_type is None:
            self.commit()
        else:
            self.rollback()


__all__ = [
    "Column",
    "ColumnError",
    "ConstraintError",
    "DatabaseError",
    "MemoryDatabase",
    "Table",
    "TableNotFound",
    "TransactionError",
]
