"""Minimal Paper Balance ledger for the Treasury foundation.

This module intentionally supports demo credits only. It does not process
real payments or store payment credentials.
"""
import sqlite3
from datetime import datetime
from db import get_conn


def init_treasury_tables():
    conn = get_conn()
    conn.execute("""CREATE TABLE IF NOT EXISTS treasury_accounts (
        owner_id TEXT PRIMARY KEY,
        balance REAL NOT NULL DEFAULT 0,
        currency TEXT NOT NULL DEFAULT 'RUB',
        updated_at TEXT NOT NULL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS treasury_ledger (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        owner_id TEXT NOT NULL,
        kind TEXT NOT NULL CHECK(kind IN ('credit', 'debit')),
        amount REAL NOT NULL CHECK(amount > 0),
        description TEXT NOT NULL,
        reference TEXT,
        created_at TEXT NOT NULL
    )""")
    conn.commit()
    conn.close()


def get_account(owner_id='default'):
    init_treasury_tables()
    conn = get_conn()
    row = conn.execute("SELECT * FROM treasury_accounts WHERE owner_id = ?", (owner_id,)).fetchone()
    if row is None:
        now = datetime.utcnow().isoformat()
        conn.execute("INSERT INTO treasury_accounts(owner_id, updated_at) VALUES (?, ?)", (owner_id, now))
        conn.commit()
        row = conn.execute("SELECT * FROM treasury_accounts WHERE owner_id = ?", (owner_id,)).fetchone()
    ledger = conn.execute("SELECT * FROM treasury_ledger WHERE owner_id = ? ORDER BY id DESC", (owner_id,)).fetchall()
    conn.close()
    return {"owner_id": row["owner_id"], "balance": row["balance"], "currency": row["currency"],
            "updated_at": row["updated_at"], "ledger": [dict(item) for item in ledger]}


def demo_top_up(owner_id, amount, description='Demo top-up'):
    amount = float(amount)
    if amount <= 0:
        raise ValueError('amount must be positive')
    init_treasury_tables()
    now = datetime.utcnow().isoformat()
    conn = get_conn()
    conn.execute("INSERT OR IGNORE INTO treasury_accounts(owner_id, updated_at) VALUES (?, ?)", (owner_id, now))
    conn.execute("UPDATE treasury_accounts SET balance = balance + ?, updated_at = ? WHERE owner_id = ?", (amount, now, owner_id))
    conn.execute("INSERT INTO treasury_ledger(owner_id, kind, amount, description, created_at) VALUES (?, 'credit', ?, ?, ?)", (owner_id, amount, description, now))
    conn.commit()
    conn.close()
    return get_account(owner_id)


def record_expense(owner_id, amount, description, reference=None):
    amount = float(amount)
    if amount <= 0 or not description:
        raise ValueError('positive amount and description are required')
    init_treasury_tables()
    now = datetime.utcnow().isoformat()
    conn = get_conn()
    account = conn.execute("SELECT balance FROM treasury_accounts WHERE owner_id = ?", (owner_id,)).fetchone()
    balance = float(account["balance"]) if account else 0.0
    if balance < amount:
        conn.close()
        raise ValueError('insufficient balance')
    conn.execute("UPDATE treasury_accounts SET balance = balance - ?, updated_at = ? WHERE owner_id = ?", (amount, now, owner_id))
    conn.execute("INSERT INTO treasury_ledger(owner_id, kind, amount, description, reference, created_at) VALUES (?, 'debit', ?, ?, ?, ?)", (owner_id, amount, description, reference, now))
    conn.commit()
    conn.close()
    return get_account(owner_id)
