"""Flask blueprint for RAG pipeline management."""

from __future__ import annotations

import io
import logging
import os

from flask import Blueprint, jsonify, request

from rag import (
    add_document,
    attach_to_conversation,
    create_pipeline,
    delete_pipeline,
    detach_from_conversation,
    get_pipeline,
    list_conversations_for_pipeline,
    list_pipelines,
    list_pipelines_for_conversation,
)
from yandex_client import YandexClientError

logger = logging.getLogger("rag_routes")
rag_bp = Blueprint("rag", __name__)

ALLOWED_EXTENSIONS = {".pdf", ".docx", ".xlsx", ".csv", ".md", ".html", ".json", ".jsonl", ".txt"}
MAX_FILE_SIZE = 128 * 1024 * 1024


def _err(e: Exception, fallback_status: int = 500):
    msg = str(e)
    status = fallback_status
    if isinstance(e, ValueError):
        status = 400
    elif isinstance(e, YandexClientError):
        if "Not found" in msg:
            status = 404
        elif "Auth" in msg:
            status = 401
        else:
            status = 502
    return jsonify({"error": msg}), status


# ---------------------------------------------------------------------------
# Pipeline CRUD
# ---------------------------------------------------------------------------

@rag_bp.route("/api/rag/pipelines", methods=["GET"])
def api_list_pipelines():
    return jsonify({"pipelines": list_pipelines()})


@rag_bp.route("/api/rag/pipelines", methods=["POST"])
def api_create_pipeline():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400
    try:
        pipeline = create_pipeline(name)
        return jsonify(pipeline), 201
    except Exception as exc:
        return _err(exc)


@rag_bp.route("/api/rag/pipelines/<pipeline_id>", methods=["GET"])
def api_get_pipeline(pipeline_id: str):
    pipeline = get_pipeline(pipeline_id)
    if pipeline is None:
        return jsonify({"error": "not found"}), 404
    return jsonify(pipeline)


@rag_bp.route("/api/rag/pipelines/<pipeline_id>", methods=["DELETE"])
def api_delete_pipeline(pipeline_id: str):
    try:
        found = delete_pipeline(pipeline_id)
        if not found:
            return jsonify({"error": "not found"}), 404
        return jsonify({"status": "deleted", "id": pipeline_id})
    except Exception as exc:
        return _err(exc)


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------

@rag_bp.route("/api/rag/pipelines/<pipeline_id>/documents", methods=["POST"])
def api_add_document(pipeline_id: str):
    if "file" not in request.files:
        return jsonify({"error": "No file in request"}), 400
    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "Empty filename"}), 400

    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return jsonify({"error": f"File type not allowed. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}"}), 400

    buf = io.BytesIO()
    f.save(buf)
    data = buf.getvalue()
    if not data:
        return jsonify({"error": "File is empty"}), 400
    if len(data) > MAX_FILE_SIZE:
        return jsonify({"error": "File too large (max 128 MB)"}), 413

    try:
        result = add_document(pipeline_id, data, f.filename)
        return jsonify(result), 201
    except (ValueError, RuntimeError) as exc:
        return _err(exc, fallback_status=400)
    except YandexClientError as exc:
        return _err(exc)
    except Exception as exc:
        logger.exception("[RAG] add_document failed")
        return _err(exc)


# ---------------------------------------------------------------------------
# Conversation attachment
# ---------------------------------------------------------------------------

@rag_bp.route("/api/rag/pipelines/<pipeline_id>/conversations", methods=["GET"])
def api_list_pipeline_convs(pipeline_id: str):
    pipeline = get_pipeline(pipeline_id)
    if pipeline is None:
        return jsonify({"error": "not found"}), 404
    return jsonify({"conversations": list_conversations_for_pipeline(pipeline_id)})


@rag_bp.route("/api/rag/pipelines/<pipeline_id>/conversations/<conversation_id>", methods=["PUT"])
def api_attach_pipeline(pipeline_id: str, conversation_id: str):
    data = request.get_json(silent=True) or {}
    try:
        max_results = int(data.get("max_results", 20))
        if max_results < 1:
            raise ValueError("max_results must be >= 1")
    except (TypeError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400
    try:
        attach_to_conversation(pipeline_id, conversation_id, max_results=max_results)
        return jsonify({"status": "attached", "pipeline_id": pipeline_id, "conversation_id": conversation_id})
    except ValueError as exc:
        return _err(exc, fallback_status=404)
    except Exception as exc:
        return _err(exc)


@rag_bp.route("/api/rag/pipelines/<pipeline_id>/conversations/<conversation_id>", methods=["DELETE"])
def api_detach_pipeline(pipeline_id: str, conversation_id: str):
    try:
        detach_from_conversation(pipeline_id, conversation_id)
        return jsonify({"status": "detached", "pipeline_id": pipeline_id, "conversation_id": conversation_id})
    except Exception as exc:
        return _err(exc)


@rag_bp.route("/api/conversations/<conversation_id>/rag", methods=["GET"])
def api_conv_rag(conversation_id: str):
    """Return all RAG pipelines attached to a conversation."""
    return jsonify({"pipelines": list_pipelines_for_conversation(conversation_id)})
