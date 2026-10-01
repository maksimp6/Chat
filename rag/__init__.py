"""RAG pipeline manager.

A RAG pipeline wraps a Yandex AI Studio vector store and links it to one or
more conversations via the existing ``file_search`` hosted tool.  When a
pipeline is attached to a conversation the route handler enables
``tools_config.file_search`` in ``conv_settings`` so ``ask_with_mcp`` sends
the vector store IDs to the Responses API on every turn.
"""

from __future__ import annotations

import io
import json
import logging
import time
import uuid

from config import Config
from db import get_conn, save_conv_settings, get_conv_settings
from yandex_client import YandexResponsesClient, YandexClientError

logger = logging.getLogger("rag")

_STATUS_CREATED = "created"
_STATUS_READY = "ready"
_STATUS_ERROR = "error"


# ---------------------------------------------------------------------------
# Schema helpers
# ---------------------------------------------------------------------------

def init_rag_tables() -> None:
    """Create the RAG tables if they do not exist yet (both SQLite and PostgreSQL)."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS rag_pipelines (
            id          TEXT    PRIMARY KEY,
            name        TEXT    NOT NULL,
            vs_id       TEXT,
            status      TEXT    NOT NULL DEFAULT 'created',
            created_at  INTEGER NOT NULL,
            updated_at  INTEGER NOT NULL
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS rag_pipeline_convs (
            pipeline_id     TEXT    NOT NULL,
            conversation_id TEXT    NOT NULL,
            max_results     INTEGER NOT NULL DEFAULT 20,
            PRIMARY KEY (pipeline_id, conversation_id)
        )
    """)
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _now() -> int:
    return int(time.time())


def _client() -> YandexResponsesClient:
    return YandexResponsesClient(Config)


def _apply_file_search(conversation_id: str, vector_store_ids: list[str], max_results: int) -> None:
    """Enable file_search in conv_settings with the given vector store IDs."""
    settings = get_conv_settings(conversation_id) or {}
    tools_config = dict(settings.get("tools_config") or {})
    tools_config["file_search"] = {
        "enabled": bool(vector_store_ids),
        "vector_store_ids": ",".join(vector_store_ids),
        "max_results": max_results,
    }
    settings["tools_config"] = tools_config
    save_conv_settings(conversation_id, settings)


def _collect_vs_ids_for_conv(conversation_id: str) -> list[str]:
    """Return the vector store IDs of all pipelines attached to a conversation."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT p.vs_id
        FROM rag_pipeline_convs c
        JOIN rag_pipelines p ON p.id = c.pipeline_id
        WHERE c.conversation_id = ?
          AND p.vs_id IS NOT NULL
          AND p.status = 'ready'
        """,
        (conversation_id,),
    )
    rows = cur.fetchall()
    conn.close()
    return [r[0] for r in rows]


def _max_results_for_conv(conversation_id: str) -> int:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT MIN(max_results) FROM rag_pipeline_convs WHERE conversation_id = ?",
        (conversation_id,),
    )
    row = cur.fetchone()
    conn.close()
    val = row[0] if row else None
    return int(val) if val is not None else 20


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def create_pipeline(name: str) -> dict:
    """Create a RAG pipeline: allocate a vector store in Yandex AI Studio and persist metadata."""
    pipeline_id = str(uuid.uuid4())
    now = _now()
    vs_id = None
    status = _STATUS_CREATED
    try:
        client = _client()
        vs = client.create_vector_store(name=name)
        vs_id = vs.get("id")
        if vs_id:
            status = _STATUS_READY
    except YandexClientError as exc:
        logger.warning("[RAG] VS creation failed, storing pipeline as created: %s", exc)

    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO rag_pipelines (id, name, vs_id, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        (pipeline_id, name, vs_id, status, now, now),
    )
    conn.commit()
    conn.close()
    return {"id": pipeline_id, "name": name, "vs_id": vs_id, "status": status, "created_at": now}


def get_pipeline(pipeline_id: str) -> dict | None:
    """Return pipeline metadata, refreshing vector store status from Yandex if ready."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id, name, vs_id, status, created_at, updated_at FROM rag_pipelines WHERE id = ?", (pipeline_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    pipeline = dict(zip(["id", "name", "vs_id", "status", "created_at", "updated_at"], row))
    if pipeline["vs_id"] and pipeline["status"] == _STATUS_READY:
        try:
            client = _client()
            vs = client.get_vector_store(pipeline["vs_id"])
            pipeline["vector_store"] = vs
        except YandexClientError:
            pass
    return pipeline


def list_pipelines() -> list[dict]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id, name, vs_id, status, created_at, updated_at FROM rag_pipelines ORDER BY created_at DESC")
    rows = cur.fetchall()
    conn.close()
    return [dict(zip(["id", "name", "vs_id", "status", "created_at", "updated_at"], r)) for r in rows]


def delete_pipeline(pipeline_id: str) -> bool:
    """Delete pipeline and its Yandex vector store.  Returns True if found."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT vs_id FROM rag_pipelines WHERE id = ?", (pipeline_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return False
    vs_id = row[0]

    # Remove conversation links and collect affected conversations.
    cur.execute("SELECT conversation_id, max_results FROM rag_pipeline_convs WHERE pipeline_id = ?", (pipeline_id,))
    affected = cur.fetchall()
    cur.execute("DELETE FROM rag_pipeline_convs WHERE pipeline_id = ?", (pipeline_id,))
    cur.execute("DELETE FROM rag_pipelines WHERE id = ?", (pipeline_id,))
    conn.commit()
    conn.close()

    # Re-sync file_search for each formerly-linked conversation.
    for conv_id, _ in affected:
        _sync_conversation_file_search(conv_id)

    if vs_id:
        try:
            _client().delete_vector_store(vs_id)
        except YandexClientError as exc:
            logger.warning("[RAG] VS deletion failed: %s", exc)
    return True


def add_document(pipeline_id: str, file_data: bytes, filename: str) -> dict:
    """Upload a file and attach it to the pipeline's vector store."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT vs_id, status FROM rag_pipelines WHERE id = ?", (pipeline_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        raise ValueError(f"Pipeline {pipeline_id!r} not found")
    vs_id, status = row
    if not vs_id:
        raise ValueError(f"Pipeline {pipeline_id!r} has no vector store (status={status})")

    client = _client()
    file_obj = io.BytesIO(file_data)
    uploaded = client.upload_file(file_obj, filename, purpose="assistants")
    file_id = uploaded.get("id")
    if not file_id:
        raise RuntimeError("File upload returned no id")

    vs_file = client.add_file_to_vs(vs_id, file_id)

    # Mark pipeline as ready if it was just in created state.
    if status != _STATUS_READY:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            "UPDATE rag_pipelines SET status = ?, updated_at = ? WHERE id = ?",
            (_STATUS_READY, _now(), pipeline_id),
        )
        conn.commit()
        conn.close()

    return {"file": uploaded, "vs_file": vs_file}


def attach_to_conversation(pipeline_id: str, conversation_id: str, max_results: int = 20) -> None:
    """Link a pipeline to a conversation and update conv_settings."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id FROM rag_pipelines WHERE id = ?", (pipeline_id,))
    if not cur.fetchone():
        conn.close()
        raise ValueError(f"Pipeline {pipeline_id!r} not found")
    cur.execute(
        """
        INSERT INTO rag_pipeline_convs (pipeline_id, conversation_id, max_results)
        VALUES (?, ?, ?)
        ON CONFLICT(pipeline_id, conversation_id) DO UPDATE SET max_results = excluded.max_results
        """,
        (pipeline_id, conversation_id, max_results),
    )
    conn.commit()
    conn.close()
    _sync_conversation_file_search(conversation_id)


def detach_from_conversation(pipeline_id: str, conversation_id: str) -> None:
    """Unlink a pipeline from a conversation and update conv_settings."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM rag_pipeline_convs WHERE pipeline_id = ? AND conversation_id = ?",
        (pipeline_id, conversation_id),
    )
    conn.commit()
    conn.close()
    _sync_conversation_file_search(conversation_id)


def list_conversations_for_pipeline(pipeline_id: str) -> list[dict]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT conversation_id, max_results FROM rag_pipeline_convs WHERE pipeline_id = ?",
        (pipeline_id,),
    )
    rows = cur.fetchall()
    conn.close()
    return [{"conversation_id": r[0], "max_results": r[1]} for r in rows]


def list_pipelines_for_conversation(conversation_id: str) -> list[dict]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT p.id, p.name, p.vs_id, p.status, c.max_results
        FROM rag_pipeline_convs c
        JOIN rag_pipelines p ON p.id = c.pipeline_id
        WHERE c.conversation_id = ?
        """,
        (conversation_id,),
    )
    rows = cur.fetchall()
    conn.close()
    return [{"id": r[0], "name": r[1], "vs_id": r[2], "status": r[3], "max_results": r[4]} for r in rows]


def _sync_conversation_file_search(conversation_id: str) -> None:
    """Re-build file_search config from all pipelines currently attached to a conversation."""
    vs_ids = _collect_vs_ids_for_conv(conversation_id)
    max_results = _max_results_for_conv(conversation_id) if vs_ids else 20
    _apply_file_search(conversation_id, vs_ids, max_results)
