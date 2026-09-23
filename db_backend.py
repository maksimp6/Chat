"""Database backend selection for Alice Pro.

SQLite remains the default local/Termux backend. PostgreSQL is strictly opt-in:
set ALICE_DATABASE_URL to switch the shared application database to PostgreSQL.
"""
from __future__ import annotations

import os
import re
import sqlite3
from typing import Any, Iterable, Optional

try:
    import psycopg
except ImportError:  # pragma: no cover - only relevant when PostgreSQL is selected
    psycopg = None


_QMARK_RE = re.compile(r"\?")
_AUTOINCREMENT_RE = re.compile(
    r"\bINTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b", re.IGNORECASE
)
_BEGIN_IMMEDIATE_RE = re.compile(r"\bBEGIN\s+IMMEDIATE\b", re.IGNORECASE)
_PRAGMA_TABLE_INFO_RE = re.compile(
    r"^\s*PRAGMA\s+table_info\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)\s*;?\s*$",
    re.IGNORECASE,
)
_ALTER_ADD_COLUMN_RE = re.compile(
    r"^\s*ALTER\s+TABLE\s+([A-Za-z_][A-Za-z0-9_]*)\s+ADD\s+COLUMN\s+"
    r"([A-Za-z_][A-Za-z0-9_]*)\s+(.+?)\s*;?\s*$",
    re.IGNORECASE | re.DOTALL,
)
_INSERT_OR_IGNORE_RE = re.compile(
    r"^\s*INSERT\s+OR\s+IGNORE\s+INTO\s+", re.IGNORECASE
)
_UNIQUE_VIOLATION_NAMES = {"UniqueViolation", "UniqueViolationError"}


class PGRow:
    """sqlite3.Row-compatible mapping with numeric and named access."""

    def __init__(self, columns: Iterable[str], values: Iterable[Any]) -> None:
        self._columns = tuple(columns)
        self._values = tuple(values)
        self._mapping = dict(zip(self._columns, self._values))

    def __getitem__(self, key: int | str) -> Any:
        if isinstance(key, int):
            return self._values[key]
        return self._mapping[key]

    def keys(self):
        return self._columns

    def items(self):
        return self._mapping.items()

    def values(self):
        return self._mapping.values()

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)

    def __repr__(self) -> str:
        return repr(self._mapping)


def translate_sql(sql: str) -> str:
    """Translate the small SQLite SQL subset used by the application."""
    text = _BEGIN_IMMEDIATE_RE.sub("BEGIN", sql)
    text = _AUTOINCREMENT_RE.sub("BIGSERIAL PRIMARY KEY", text)

    if _INSERT_OR_IGNORE_RE.match(text):
        text = _INSERT_OR_IGNORE_RE.sub("INSERT INTO ", text, count=1)
        if not re.search(r"\bON\s+CONFLICT\b", text, re.IGNORECASE):
            text = text.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"

    # psycopg uses '%' for parameter interpolation. Escape literal '%' from
    # SQLite SQL (for example LIKE 'bootstrap-%') before adding %s placeholders.
    marker = "__ALICE_QMARK_PLACEHOLDER__"
    text = _QMARK_RE.sub(marker, text)
    text = text.replace("%", "%%")
    return text.replace(marker, "%s")


class PGCursor:
    def __init__(self, raw_cursor) -> None:
        self._raw = raw_cursor
        self._columns: tuple[str, ...] = ()

    @property
    def rowcount(self) -> int:
        return self._raw.rowcount

    @property
    def lastrowid(self) -> Optional[int]:
        return None

    @property
    def description(self):
        return self._raw.description

    def execute(self, sql: str, params: Iterable[Any] = ()) -> "PGCursor":
        pragma_match = _PRAGMA_TABLE_INFO_RE.match(sql)
        if pragma_match:
            table = pragma_match.group(1)
            self._raw.execute(
                """
                SELECT
                    (ordinal_position - 1) AS cid,
                    column_name AS name,
                    data_type AS type,
                    CASE WHEN is_nullable = 'NO' THEN 1 ELSE 0 END AS notnull,
                    column_default AS dflt_value,
                    0 AS pk
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = %s
                ORDER BY ordinal_position
                """,
                (table,),
            )
            self._columns = tuple(desc.name for desc in self._raw.description)
            return self

        alter_match = _ALTER_ADD_COLUMN_RE.match(sql)
        translated = (
            f"ALTER TABLE {alter_match.group(1)} ADD COLUMN IF NOT EXISTS "
            f"{alter_match.group(2)} {alter_match.group(3)}"
            if alter_match
            else translate_sql(sql)
        )

        try:
            self._raw.execute(translated, tuple(params))
        except Exception as exc:
            if exc.__class__.__name__ in _UNIQUE_VIOLATION_NAMES:
                raise sqlite3.IntegrityError(str(exc)) from exc
            raise
        self._columns = tuple(desc.name for desc in self._raw.description or ())
        return self

    def executemany(self, sql: str, seq_of_params) -> "PGCursor":
        translated = translate_sql(sql)
        try:
            self._raw.executemany(translated, seq_of_params)
        except Exception as exc:
            if exc.__class__.__name__ in _UNIQUE_VIOLATION_NAMES:
                raise sqlite3.IntegrityError(str(exc)) from exc
            raise
        self._columns = tuple(desc.name for desc in self._raw.description or ())
        return self

    def fetchone(self):
        row = self._raw.fetchone()
        return None if row is None else PGRow(self._columns, row)

    def fetchall(self):
        return [PGRow(self._columns, row) for row in self._raw.fetchall()]

    def fetchmany(self, size=None):
        rows = self._raw.fetchmany(size) if size is not None else self._raw.fetchmany()
        return [PGRow(self._columns, row) for row in rows]

    def close(self) -> None:
        self._raw.close()


class PGConnection:
    """Connection wrapper exposing the subset of sqlite3.Connection used here."""

    def __init__(self, url: str) -> None:
        if psycopg is None:
            raise RuntimeError(
                "PostgreSQL backend selected but psycopg is not installed"
            )
        self._raw = psycopg.connect(url)
        self._row_factory = None

    @property
    def row_factory(self):
        return self._row_factory

    @row_factory.setter
    def row_factory(self, value):
        self._row_factory = value

    def execute(self, sql: str, params: Iterable[Any] = ()) -> PGCursor:
        return PGCursor(self._raw.cursor()).execute(sql, params)

    def executemany(self, sql: str, seq_of_params) -> PGCursor:
        return PGCursor(self._raw.cursor()).executemany(sql, seq_of_params)

    def cursor(self) -> PGCursor:
        return PGCursor(self._raw.cursor())

    def commit(self) -> None:
        self._raw.commit()

    def rollback(self) -> None:
        self._raw.rollback()

    def close(self) -> None:
        self._raw.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        self.close()


def postgres_url_from_env() -> str:
    # Deliberately do not fall back to DATABASE_URL. PostgreSQL must stay opt-in.
    return os.getenv("ALICE_DATABASE_URL", "").strip()


def connect_postgres(url: Optional[str] = None) -> PGConnection:
    value = (url or postgres_url_from_env()).strip()
    if not value:
        raise ValueError("ALICE_DATABASE_URL is not configured")
    return PGConnection(value)


def is_postgres_configured() -> bool:
    return bool(postgres_url_from_env())
