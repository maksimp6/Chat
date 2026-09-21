"""Autonomous MCP servers storage — with per-conversation settings."""
import os
import uuid
import json
import logging

from db import get_conn

logger = logging.getLogger("mcp_storage")

DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "mcp_servers.db"))

def _get_conn():
    try:
        return get_conn()
    except Exception as e:
        logger.exception(f"CRITICAL: Cannot connect to DB {DB_PATH}: {e}")
        raise

def init_db():
    logger.info(f"Initializing MCP storage at: {DB_PATH}")
    try:
        conn = _get_conn()
        conn.execute('''
            CREATE TABLE IF NOT EXISTS mcp_servers (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                server_url TEXT DEFAULT '',
                connector_id TEXT DEFAULT '',
                transport TEXT DEFAULT 'streamable',
                server_label TEXT DEFAULT '',
                server_description TEXT DEFAULT '',
                authorization TEXT DEFAULT '',
                headers TEXT DEFAULT '',
                allowed_tools TEXT DEFAULT '',
                allowed_tools_read_only INTEGER DEFAULT 0,
                require_approval TEXT DEFAULT 'always',
                require_approval_tools TEXT DEFAULT '',
                require_approval_read_only INTEGER DEFAULT 0,
                require_approval_never_tools TEXT DEFAULT '',
                require_approval_never_read_only INTEGER DEFAULT 0,
                defer_loading INTEGER DEFAULT 0,
                config TEXT DEFAULT '{}',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS conv_mcp_settings (
                conversation_id TEXT PRIMARY KEY,
                enabled_mcp_servers TEXT DEFAULT '[]',
                mcp_approval TEXT DEFAULT '{}',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.commit()
        conn.close()
        logger.info("OK: Tables ready.")
    except Exception as e:
        logger.exception(f"CRITICAL: DB init failed: {e}")

init_db()

def _row_to_dict(r):
    return {
        'id': r['id'], 'name': r['name'], 'server_url': r['server_url'],
        'connector_id': r['connector_id'], 'transport': r['transport'],
        'server_label': r['server_label'], 'server_description': r['server_description'],
        'authorization': r['authorization'], 'headers': r['headers'],
        'allowed_tools': r['allowed_tools'],
        'allowed_tools_read_only': bool(r['allowed_tools_read_only']),
        'require_approval': r['require_approval'], 'require_approval_tools': r['require_approval_tools'],
        'require_approval_read_only': bool(r['require_approval_read_only']),
        'require_approval_never_tools': r['require_approval_never_tools'],
        'require_approval_never_read_only': bool(r['require_approval_never_read_only']),
        'defer_loading': bool(r['defer_loading']),
        'config': json.loads(r['config'] or '{}'),
    }

def list_servers():
    conn = _get_conn()
    try:
        rows = conn.execute('SELECT * FROM mcp_servers ORDER BY created_at DESC').fetchall()
        return [_row_to_dict(r) for r in rows]
    except Exception as e:
        logger.exception(f"Error reading server list: {e}")
        return []
    finally:
        conn.close()

def create_server(data):
    conn = _get_conn()
    try:
        sid = str(uuid.uuid4())
        conn.execute(
            '''INSERT INTO mcp_servers
               (id, name, server_url, connector_id, transport, server_label, server_description,
                authorization, headers, allowed_tools, allowed_tools_read_only,
                require_approval, require_approval_tools, require_approval_read_only,
                require_approval_never_tools, require_approval_never_read_only, defer_loading, config)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (sid, data['name'], data['server_url'], data['connector_id'],
             data.get('transport', 'streamable'),
             data['server_label'], data['server_description'], data['authorization'], data['headers'],
             data['allowed_tools'], data['allowed_tools_read_only'], data['require_approval'],
             data['require_approval_tools'], data['require_approval_read_only'],
             data['require_approval_never_tools'], data['require_approval_never_read_only'],
             data['defer_loading'], json.dumps(data.get('config', {}), ensure_ascii=False))
        )
        conn.commit()
        return sid
    except Exception as e:
        logger.exception(f"Error creating server: {e}")
        raise
    finally:
        conn.close()

def get_server(server_id):
    conn = _get_conn()
    try:
        row = conn.execute('SELECT * FROM mcp_servers WHERE id=?', (server_id,)).fetchone()
        return _row_to_dict(row) if row else None
    except Exception as e:
        logger.exception(f"Error finding server {server_id}: {e}")
        return None
    finally:
        conn.close()

def update_server(server_id, data):
    conn = _get_conn()
    try:
        conn.execute(
            '''UPDATE mcp_servers SET
               name=?, server_url=?, connector_id=?, transport=?, server_label=?, server_description=?,
               authorization=?, headers=?, allowed_tools=?, allowed_tools_read_only=?,
               require_approval=?, require_approval_tools=?, require_approval_read_only=?,
               require_approval_never_tools=?, require_approval_never_read_only=?, defer_loading=?,
               config=?
               WHERE id=?''',
            (data['name'], data['server_url'], data['connector_id'],
             data.get('transport', 'streamable'),
             data['server_label'], data['server_description'], data['authorization'], data['headers'],
             data['allowed_tools'], data['allowed_tools_read_only'], data['require_approval'],
             data['require_approval_tools'], data['require_approval_read_only'],
             data['require_approval_never_tools'], data['require_approval_never_read_only'],
             data['defer_loading'], json.dumps(data.get('config', {}), ensure_ascii=False), server_id)
        )
        conn.commit()
    except Exception as e:
        logger.exception(f"Error updating server {server_id}: {e}")
        raise
    finally:
        conn.close()

def delete_server(server_id):
    conn = _get_conn()
    try:
        conn.execute('DELETE FROM mcp_servers WHERE id=?', (server_id,))
        conn.commit()
    except Exception as e:
        logger.exception(f"Error deleting server {server_id}: {e}")
        raise
    finally:
        conn.close()

def get_conv_mcp_settings(conv_id):
    conn = _get_conn()
    try:
        row = conn.execute('SELECT * FROM conv_mcp_settings WHERE conversation_id=?', (conv_id,)).fetchone()
        if row:
            return {
                'enabled_mcp_servers': json.loads(row['enabled_mcp_servers'] or '[]'),
                'mcp_approval': json.loads(row['mcp_approval'] or '{}'),
            }
        return {'enabled_mcp_servers': [], 'mcp_approval': {}}
    except Exception as e:
        logger.exception(f"Error reading conv MCP settings for {conv_id}: {e}")
        return {'enabled_mcp_servers': [], 'mcp_approval': {}}
    finally:
        conn.close()

def save_conv_mcp_settings(conv_id, enabled_ids, approval_map):
    conn = _get_conn()
    try:
        conn.execute('''
            INSERT INTO conv_mcp_settings (conversation_id, enabled_mcp_servers, mcp_approval, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(conversation_id) DO UPDATE SET
                enabled_mcp_servers=excluded.enabled_mcp_servers,
                mcp_approval=excluded.mcp_approval,
                updated_at=CURRENT_TIMESTAMP
        ''', (conv_id, json.dumps(enabled_ids), json.dumps(approval_map)))
        conn.commit()
    except Exception as e:
        logger.exception(f"Error saving conv MCP settings for {conv_id}: {e}")
        raise
    finally:
        conn.close()

def get_enabled_servers_for_conv(conv_id):
    conn = _get_conn()
    try:
        row = conn.execute('SELECT enabled_mcp_servers FROM conv_mcp_settings WHERE conversation_id=?', (conv_id,)).fetchone()
        if not row:
            return []
        enabled_ids = json.loads(row['enabled_mcp_servers'] or '[]')
        if not enabled_ids:
            return []
        placeholders = ','.join('?' * len(enabled_ids))
        rows = conn.execute(
            f'SELECT * FROM mcp_servers WHERE id IN ({placeholders})', enabled_ids
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    except Exception as e:
        logger.exception(f"Error getting enabled servers for conv {conv_id}: {e}")
        return []
    finally:
        conn.close()

DEFAULT_CONFIGS = {
    "local_git": {
        "repo_path": "/sdcard/alice_pro",
        "timeout": 15,
        "default_log_limit": 5,
        "allowed_commands": ["status", "log", "diff", "branch", "show"],
    },
}

def get_server_config(server_id: str) -> dict:
    conn = _get_conn()
    try:
        row = conn.execute("SELECT connector_id, config FROM mcp_servers WHERE id = ?", (server_id,)).fetchone()
        if not row:
            return {}
        connector_id = row['connector_id']
        raw = row['config']
        try:
            overrides = json.loads(raw or "{}")
        except json.JSONDecodeError:
            overrides = {}
        base = DEFAULT_CONFIGS.get(connector_id, {})
        return {**base, **overrides}
    except Exception as e:
        logger.exception(f"Error getting config for {server_id}: {e}")
        return {}
    finally:
        conn.close()

def set_server_config(server_id: str, cfg: dict) -> None:
    conn = _get_conn()
    try:
        conn.execute("UPDATE mcp_servers SET config = ? WHERE id = ?",
            (json.dumps(cfg, ensure_ascii=False), server_id))
        conn.commit()
    except Exception as e:
        logger.exception(f"Error setting config for {server_id}: {e}")
        raise
    finally:
        conn.close()
