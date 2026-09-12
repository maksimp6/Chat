import sqlite3
import os
from datetime import datetime

DB_PATH = "alice_pro.db"

def get_conn():
    conn = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Создаёт таблицы, если их нет (запуск при старте)."""
    conn = get_conn()
    cur = conn.cursor()

    # conversations
    cur.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            model TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        )
    """)

    # messages
    cur.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            cost REAL DEFAULT 0.0,
            FOREIGN KEY (conversation_id) REFERENCES conversations(id)
        )
    """)

    # conv_settings — новые настройки диалога (все параметры, кроме MCP)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS conv_settings (
            conversation_id TEXT PRIMARY KEY,
            settings_json TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

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
        "INSERT INTO conversations (id, title, model, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
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
    cur.execute("SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at ASC", (conv_id,))
    rows = cur.fetchall()
    conn.close()
    return [
        {
            "role": r["role"],
            "text": r["content"],
            "cost": r["cost"],
            "created_at": r["created_at"]
        }
        for r in rows
    ]

def add_message(conv_id, role, content, cost=0.0, usage=None, model=None, source=None):
    now = int(datetime.utcnow().timestamp())
    import json
    if not isinstance(content, str):
        content = json.dumps(content, ensure_ascii=False) if content is not None else ""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO messages (conversation_id, role, content, created_at, cost)
        VALUES (?, ?, ?, ?, ?)
        """,
        (conv_id, role, content, now, cost)
    )
    conn.commit()
    conn.close()

# --- Новые функции для conv_settings ---

def save_conv_settings(conv_id, settings_dict):
    import json
    now = datetime.utcnow()
    settings_json = json.dumps(settings_dict, ensure_ascii=False)
    conn = get_conn()
    cur = conn.cursor()
    # UPSERT: если есть — обновит, если нет — вставит
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
    import json
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
