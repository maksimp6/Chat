import os
import json
import sqlite3
from datetime import datetime
from pathlib import Path

from db_backend import connect_postgres, postgres_url_from_env
from memory_db import Column, MemoryDatabase
from runtime.request_context import current_runtime_data_root


DB_PATH = os.getenv("ALICE_DB_PATH", "alice_pro.db")
_MEMORY_DB = MemoryDatabase()
_MEMORY_INITIALIZED = False


def _runtime_db_path():
    data_root = current_runtime_data_root()
    if not data_root:
        return None
    root = Path(data_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return str(root / "alice_pro.db")


def is_memory_configured():
    if current_runtime_data_root():
        return False
    return os.getenv("ALICE_DB_BACKEND", "").strip().lower() == "memory"


def _memory_setup():
    global _MEMORY_INITIALIZED
    if _MEMORY_INITIALIZED:
        return
    _MEMORY_DB.create_table(
        "conversations",
        [
            Column("id", str, nullable=False, unique=True),
            Column("title", str, nullable=False),
            Column("model", str, nullable=False),
            Column("created_at", int, nullable=False),
            Column("updated_at", int, nullable=False),
        ],
    )
    _MEMORY_DB.create_table(
        "messages",
        [
            Column("id", int, nullable=False, unique=True),
            Column("conversation_id", str, nullable=False),
            Column("role", str, nullable=False),
            Column("content", str, nullable=False),
            Column("created_at", int, nullable=False),
            Column("cost", (int, float), nullable=False, default=0.0),
            Column("timings_json", str, nullable=False, default="[]"),
            Column("trace_json", str, nullable=False, default="{}"),
        ],
    )
    _MEMORY_DB.create_table(
        "conv_settings",
        [
            Column("conversation_id", str, nullable=False, unique=True),
            Column("settings_json", str, nullable=False),
            Column("updated_at", int, nullable=False),
        ],
    )
    _MEMORY_DB.create_table(
        "configs",
        [
            Column("key", str, nullable=False, unique=True),
            Column("value", str, nullable=False),
        ],
    )
    _MEMORY_DB.create_table(
        "conv_yandex_map",
        [
            Column("local_id", str, nullable=False, unique=True),
            Column("yandex_id", str, nullable=False),
        ],
    )
    _MEMORY_INITIALIZED = True


def reset_memory_db():
    global _MEMORY_INITIALIZED
    _MEMORY_DB.reset()
    _MEMORY_INITIALIZED = False
    _memory_setup()


def _next_message_id():
    rows = _MEMORY_DB.select("messages")
    return max((row["id"] for row in rows), default=0) + 1


def get_conn():
    runtime_db_path = _runtime_db_path()
    if runtime_db_path:
        conn = sqlite3.connect(
            runtime_db_path,
            detect_types=sqlite3.PARSE_DECLTYPES,
            timeout=15,
        )
    else:
        if is_memory_configured():
            raise RuntimeError(
                "memory backend does not expose a SQL connection; use db API functions"
            )
        database_url = postgres_url_from_env()
        if database_url:
            return connect_postgres(database_url)
        conn = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db():
    if is_memory_configured():
        _memory_setup()
        return
    from provider_credentials import create_schema as create_provider_credentials_schema
    from key_manager import create_schema as create_key_manager_schema

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            model TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            cost REAL DEFAULT 0.0,
            timings_json TEXT DEFAULT '[]',
            trace_json TEXT DEFAULT '{}',
            FOREIGN KEY (conversation_id) REFERENCES conversations(id)
        )
    """)

    try:
        cur.execute("ALTER TABLE messages ADD COLUMN timings_json TEXT DEFAULT '[]'")
    except sqlite3.OperationalError:
        pass

    try:
        cur.execute("ALTER TABLE messages ADD COLUMN trace_json TEXT DEFAULT '{}'")
    except sqlite3.OperationalError:
        pass

    cur.execute("""
        CREATE TABLE IF NOT EXISTS conv_settings (
            conversation_id TEXT PRIMARY KEY,
            settings_json TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Shared tables that were historically created lazily by individual modules.
    # Bootstrap them here so every selected backend starts with the same schema.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS configs (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS conv_yandex_map (
            local_id TEXT PRIMARY KEY,
            yandex_id TEXT NOT NULL
        )
    """)

    create_provider_credentials_schema(conn)
    create_key_manager_schema(conn)

    cur.execute(
        "UPDATE conversations SET title = ? WHERE title = ?",
        ("Новый чат", "Новый диалог"),
    )

    conn.commit()
    conn.close()


def get_conversations():
    if is_memory_configured():
        _memory_setup()
        rows = sorted(
            _MEMORY_DB.select("conversations"), key=lambda r: r["updated_at"], reverse=True
        )
        return [
            {
                "id": r["id"],
                "title": r["title"],
                "model": r["model"],
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
            }
            for r in rows
        ]
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM conversations ORDER BY updated_at DESC")
    rows = cur.fetchall()
    conn.close()
    return [
        {
            "id": r["id"],
            "title": r["title"],
            "model": r["model"],
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
        }
        for r in rows
    ]


def create_conversation(conv_id, title, model):
    now = int(datetime.utcnow().timestamp())
    if is_memory_configured():
        _memory_setup()
        updated = _MEMORY_DB.update(
            "conversations",
            lambda r: r["id"] == conv_id,
            title=title,
            model=model,
            created_at=now,
            updated_at=now,
        )
        if not updated:
            _MEMORY_DB.insert(
                "conversations",
                id=conv_id,
                title=title,
                model=model,
                created_at=now,
                updated_at=now,
            )
        return
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO conversations
        (id, title, model, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            title = excluded.title,
            model = excluded.model,
            created_at = excluded.created_at,
            updated_at = excluded.updated_at
        """,
        (conv_id, title, model, now, now),
    )
    conn.commit()
    conn.close()


def normalize_conversation_title(title, max_length=80):
    """Normalize a conversation title into a compact server-authoritative label."""
    value = " ".join(str(title or "").strip().split())
    if not value:
        return "Новый чат"
    if len(value) <= max_length:
        return value
    return value[: max_length - 1].rstrip() + "…"


def get_conversation_title(conv_id, default=None):
    if is_memory_configured():
        rows = _MEMORY_DB.select("conversations", lambda r: r["id"] == conv_id)
        return rows[0]["title"] if rows else default
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT title FROM conversations WHERE id = ?", (conv_id,))
    row = cur.fetchone()
    conn.close()
    return row["title"] if row else default


def update_conversation_title(conv_id, title, owner_id=None):
    """Persist an explicit title; owner authorization is enforced by the route layer."""
    normalized = normalize_conversation_title(title)
    if is_memory_configured():
        _MEMORY_DB.update(
            "conversations",
            lambda r: r["id"] == conv_id,
            title=normalized,
            updated_at=int(datetime.utcnow().timestamp()),
        )
        return
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
        (normalized, int(datetime.utcnow().timestamp()), conv_id),
    )
    conn.commit()
    conn.close()


def maybe_update_conversation_title(conv_id, source_text):
    """Set a useful title only while the conversation still has a default title."""
    text = str(source_text or "").strip()
    if not text:
        return get_conversation_title(conv_id)

    first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    if not first_line:
        return get_conversation_title(conv_id)

    candidate = normalize_conversation_title(first_line)
    if is_memory_configured():
        changed = _MEMORY_DB.update(
            "conversations",
            lambda r: r["id"] == conv_id and r["title"] in ("Новый чат", "Новый диалог"),
            title=candidate,
            updated_at=int(datetime.utcnow().timestamp()),
        )
        return candidate if changed else get_conversation_title(conv_id)
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE conversations
           SET title = ?, updated_at = ?
         WHERE id = ? AND title IN (?, ?)
        """,
        (
            candidate,
            int(datetime.utcnow().timestamp()),
            conv_id,
            "Новый чат",
            "Новый диалог",
        ),
    )
    changed = cur.rowcount > 0
    conn.commit()
    conn.close()
    return candidate if changed else get_conversation_title(conv_id)


def update_conversation_model(conv_id, model):
    if is_memory_configured():
        _MEMORY_DB.update(
            "conversations",
            lambda r: r["id"] == conv_id,
            model=model,
            updated_at=int(datetime.utcnow().timestamp()),
        )
        return
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "UPDATE conversations SET model = ?, updated_at = ? WHERE id = ?",
        (model, int(datetime.utcnow().timestamp()), conv_id),
    )
    conn.commit()
    conn.close()


def delete_conversation(conv_id):
    if is_memory_configured():
        _MEMORY_DB.delete("messages", lambda r: r["conversation_id"] == conv_id)
        _MEMORY_DB.delete("conv_settings", lambda r: r["conversation_id"] == conv_id)
        _MEMORY_DB.delete("conversations", lambda r: r["id"] == conv_id)
        return
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
    cur.execute("DELETE FROM messages WHERE conversation_id = ?", (conv_id,))
    cur.execute("DELETE FROM conv_settings WHERE conversation_id = ?", (conv_id,))
    conn.commit()
    conn.close()


def get_messages(conv_id):
    if is_memory_configured():
        rows = sorted(
            _MEMORY_DB.select("messages", lambda r: r["conversation_id"] == conv_id),
            key=lambda r: r["id"],
        )
        result = []
        for r in rows:
            try:
                timings = json.loads(r["timings_json"])
            except Exception:
                timings = []
            try:
                trace = json.loads(r["trace_json"])
            except Exception:
                trace = {}
            result.append(
                {
                    "role": r["role"],
                    "text": r["content"],
                    "cost": r["cost"],
                    "created_at": r["created_at"],
                    "timings": timings,
                    "trace": trace,
                }
            )
        return result
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM messages WHERE conversation_id = ? ORDER BY id ASC",
        (conv_id,),
    )
    rows = cur.fetchall()
    conn.close()

    res = []
    for r in rows:
        t_json = r["timings_json"] if "timings_json" in r.keys() and r["timings_json"] else "[]"
        try:
            parsed_timings = json.loads(t_json)
        except Exception:
            parsed_timings = []

        tr_json = r["trace_json"] if "trace_json" in r.keys() and r["trace_json"] else "{}"
        try:
            parsed_trace = json.loads(tr_json)
        except Exception:
            parsed_trace = {}

        res.append(
            {
                "role": r["role"],
                "text": r["content"],
                "cost": r["cost"],
                "created_at": r["created_at"],
                "timings": parsed_timings,
                "trace": parsed_trace,
            }
        )
    return res


def add_message(
    conv_id,
    role,
    content,
    cost=0.0,
    timings=None,
    usage=None,
    model=None,
    source=None,
    trace=None,
):
    now = int(datetime.utcnow().timestamp())
    if is_memory_configured():
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False) if content is not None else ""
        _MEMORY_DB.insert(
            "messages",
            id=_next_message_id(),
            conversation_id=conv_id,
            role=role,
            content=content,
            created_at=now,
            cost=cost,
            timings_json=json.dumps(timings or [], ensure_ascii=False),
            trace_json=json.dumps(trace or {}, ensure_ascii=False),
        )
        _MEMORY_DB.update("conversations", lambda r: r["id"] == conv_id, updated_at=now)
        return
    if not isinstance(content, str):
        content = json.dumps(content, ensure_ascii=False) if content is not None else ""

    timings_str = json.dumps(timings or [], ensure_ascii=False)
    trace_str = json.dumps(trace or {}, ensure_ascii=False)

    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO messages
        (conversation_id, role, content, created_at, cost, timings_json, trace_json)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (conv_id, role, content, now, cost, timings_str, trace_str),
    )
    cur.execute(
        "UPDATE conversations SET updated_at = ? WHERE id = ?",
        (now, conv_id),
    )
    conn.commit()
    conn.close()


def save_conv_settings(conv_id, settings_dict):
    now = int(datetime.utcnow().timestamp())
    if is_memory_configured():
        payload = json.dumps(settings_dict, ensure_ascii=False)
        updated = _MEMORY_DB.update(
            "conv_settings",
            lambda r: r["conversation_id"] == conv_id,
            settings_json=payload,
            updated_at=now,
        )
        if not updated:
            _MEMORY_DB.insert(
                "conv_settings", conversation_id=conv_id, settings_json=payload, updated_at=now
            )
        return
    settings_json = json.dumps(settings_dict, ensure_ascii=False)
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO conv_settings (conversation_id, settings_json, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(conversation_id) DO UPDATE SET
            settings_json = excluded.settings_json,
            updated_at = excluded.updated_at
        """,
        (conv_id, settings_json, now),
    )
    conn.commit()
    conn.close()


def get_conv_settings(conv_id):
    if is_memory_configured():
        rows = _MEMORY_DB.select("conv_settings", lambda r: r["conversation_id"] == conv_id)
        if not rows:
            return None
        try:
            return json.loads(rows[0]["settings_json"])
        except Exception:
            return None
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT settings_json FROM conv_settings WHERE conversation_id = ?",
        (conv_id,),
    )
    row = cur.fetchone()
    conn.close()

    if not row:
        return None

    try:
        return json.loads(row["settings_json"])
    except Exception:
        return None


def init_config_table():
    if is_memory_configured():
        _memory_setup()
        return
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS configs (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()


def get_config(key: str, default=None):
    init_config_table()
    if is_memory_configured():
        rows = _MEMORY_DB.select("configs", lambda r: r["key"] == key)
        if not rows:
            return default
        try:
            return json.loads(rows[0]["value"])
        except Exception:
            return rows[0]["value"]
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT value FROM configs WHERE key = ?", (key,))
    row = cur.fetchone()
    conn.close()

    if not row:
        return default

    try:
        return json.loads(row[0])
    except Exception:
        return row[0]


def set_config(key: str, value):
    init_config_table()
    val_str = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    if is_memory_configured():
        if _MEMORY_DB.update("configs", lambda r: r["key"] == key, value=val_str) == 0:
            _MEMORY_DB.insert("configs", key=key, value=val_str)
        return
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO configs (key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, val_str),
    )
    conn.commit()
    conn.close()
