import sqlite3
import os
import json
from datetime import datetime

DB_PATH = "alice_pro.db"

def get_conn():
    conn = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn

def init_db():
    from provider_credentials import create_schema as create_provider_credentials_schema
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

    # Миграция: добавляем timings_json если таблица уже существовала
    try:
        cur.execute("ALTER TABLE messages ADD COLUMN timings_json TEXT DEFAULT '[]'")
    except sqlite3.OperationalError:
        pass

    # Миграция: добавляем trace_json если таблица уже существовала
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

    create_provider_credentials_schema(conn)
    conn.commit()
    conn.close()

def get_conversations():
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
            "updated_at": r["updated_at"]
        }
        for r in rows
    ]

def create_conversation(conv_id, title, model):
    now = int(datetime.utcnow().timestamp())
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT OR REPLACE INTO conversations (id, title, model, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        (conv_id, title, model, now, now)
    )
    conn.commit()
    conn.close()

def update_conversation_title(conv_id, title):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
        (title, int(datetime.utcnow().timestamp()), conv_id)
    )
    conn.commit()
    conn.close()

def update_conversation_model(conv_id, model):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "UPDATE conversations SET model = ?, updated_at = ? WHERE id = ?",
        (model, int(datetime.utcnow().timestamp()), conv_id)
    )
    conn.commit()
    conn.close()

def delete_conversation(conv_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
    cur.execute("DELETE FROM messages WHERE conversation_id = ?", (conv_id,))
    cur.execute("DELETE FROM conv_settings WHERE conversation_id = ?", (conv_id,))
    conn.commit()
    conn.close()

def get_messages(conv_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM messages WHERE conversation_id = ? ORDER BY id ASC", (conv_id,))
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
        res.append({
            "role": r["role"],
            "text": r["content"],
            "cost": r["cost"],
            "created_at": r["created_at"],
            "timings": parsed_timings,
            "trace": parsed_trace
        })
    return res

def add_message(conv_id, role, content, cost=0.0, timings=None, usage=None, model=None, source=None, trace=None):
    now = int(datetime.utcnow().timestamp())
    if not isinstance(content, str):
        content = json.dumps(content, ensure_ascii=False) if content is not None else ""
    timings_str = json.dumps(timings or [], ensure_ascii=False)
    trace_str = json.dumps(trace or {}, ensure_ascii=False)
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO messages (conversation_id, role, content, created_at, cost, timings_json, trace_json)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (conv_id, role, content, now, cost, timings_str, trace_str)
    )
    cur.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conv_id))
    conn.commit()
    conn.close()

def save_conv_settings(conv_id, settings_dict):
    now = datetime.utcnow()
    settings_json = json.dumps(settings_dict, ensure_ascii=False)
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO conv_settings (conversation_id, settings_json, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(conversation_id) DO UPDATE SET
            settings_json = excluded.settings_json,
            updated_at = excluded.updated_at
    """, (conv_id, settings_json, now))
    conn.commit()
    conn.close()

def get_conv_settings(conv_id):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT settings_json FROM conv_settings WHERE conversation_id = ?", (conv_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    try:
        return json.loads(row["settings_json"])
    except Exception:
        return None

# --- Подсистема конфигураций в SQLite ---
import json

def init_config_table():
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
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO configs (key, value) VALUES (?, ?)", (key, val_str))
    conn.commit()
    conn.close()
