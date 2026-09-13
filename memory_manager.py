import json
import sqlite3
from db import get_conn, get_config, set_config
from memory_extractor import init_global_memory

DEFAULT_CONFIG = {
    "enabled": True,
    "max_context_facts": 15,
    "auto_extraction": True,
    "categories_allowed": ["git", "paths", "architecture", "tools", "general"]
}

def load_memory_config() -> dict:
    cfg = get_config("memory_config")
    if not cfg:
        set_config("memory_config", DEFAULT_CONFIG)
        return DEFAULT_CONFIG
    if isinstance(cfg, str):
        try:
            return json.loads(cfg)
        except Exception:
            return DEFAULT_CONFIG
    return cfg

def save_memory_config(new_config: dict):
    current = load_memory_config()
    current.update(new_config)
    set_config("memory_config", current)

def get_controlled_memory_summary() -> str:
    cfg = load_memory_config()
    if not cfg.get("enabled", True):
        return ""

    init_global_memory()
    limit = cfg.get("max_context_facts", 15)
    allowed_cats = cfg.get("categories_allowed", [])

    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    
    if allowed_cats:
        placeholders = ",".join(["?"] * len(allowed_cats))
        cur.execute(f"SELECT category, fact FROM global_memory WHERE category IN ({placeholders}) ORDER BY updated_at DESC LIMIT ?", (*allowed_cats, limit))
    else:
        cur.execute("SELECT category, fact FROM global_memory ORDER BY updated_at DESC LIMIT ?", (limit,))
        
    rows = cur.fetchall()
    conn.close()

    if not rows:
        return ""

    summary = "\n[ГЛОБАЛЬНАЯ ПАМЯТЬ ПРОЕКТОВ]\n"
    for r in rows:
        summary += f"- [{r['category'].upper()}] {r['fact']}\n"
    return summary

def clear_global_memory(category: str = None):
    init_global_memory()
    conn = get_conn()
    cur = conn.cursor()
    if category:
        cur.execute("DELETE FROM global_memory WHERE category = ?", (category,))
    else:
        cur.execute("DELETE FROM global_memory")
    conn.commit()
    conn.close()
