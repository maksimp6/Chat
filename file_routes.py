"""Flask routes for Files and Vector Stores API"""

import os
import io
import logging
from flask import Blueprint, request, jsonify, Response
from config import Config
from yandex_client import YandexResponsesClient, YandexClientError

logger = logging.getLogger("alice_pro")
file_bp = Blueprint("file_manager", __name__)

ALLOWED_EXTENSIONS = {".pdf", ".docx", ".xlsx", ".csv", ".md", ".html", ".json", ".jsonl", ".txt"}
MAX_FILE_SIZE = 128 * 1024 * 1024  # 128 MB


def get_client():
    """Return a request-local Yandex client instead of a process-global Session."""
    return YandexResponsesClient(Config)


def _err_response(e):
    code = getattr(e, "status_code", None) or (404 if "Not found" in str(e) else 500)
    return jsonify({"error": str(e)}), code


@file_bp.route("/api/files", methods=["GET"])
def list_files():
    try:
        limit = request.args.get("limit", 100, type=int)
        after = request.args.get("after", None)
        client = get_client()
        data = client.list_files(limit=limit, after=after)
        return jsonify(data)
    except YandexClientError as e:
        return _err_response(e)


@file_bp.route("/api/files", methods=["POST"])
def upload_file():
    if "file" not in request.files:
        return jsonify({"error": "No file part in request"}), 400
    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "No selected file"}), 400
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return jsonify(
            {"error": f"File type not allowed. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}"}
        ), 400
    content_length = request.content_length
    if content_length and content_length > MAX_FILE_SIZE:
        return jsonify({"error": "File too large (max 128 MB)"}), 413
    file_stream = io.BytesIO()
    file.save(file_stream)
    file_stream.seek(0)
    file_content = file_stream.getvalue()
    if len(file_content) == 0:
        return jsonify({"error": "File is empty (0 bytes)"}), 400
    if len(file_content) > MAX_FILE_SIZE:
        return jsonify({"error": "File too large (max 128 MB)"}), 413
    try:
        client = get_client()
        purpose = request.form.get("purpose", "assistants")
        logger.info(
            f"[FILES] Uploading '{file.filename}', size: {len(file_content)} bytes to Yandex API..."
        )
        result = client.upload_file(file_content, file.filename, purpose=purpose)
        logger.info(f"[FILES] Successfully uploaded: {result.get('id')}")
        return jsonify(result), 201
    except YandexClientError as e:
        logger.error(f"[FILES] Yandex API error: {e}")
        return _err_response(e)
    except Exception as e:
        logger.error(f"[FILES] Internal upload error: {e}", exc_info=True)
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500


@file_bp.route("/api/files/<file_id>", methods=["DELETE"])
def delete_file(file_id):
    try:
        client = get_client()
        result = client.delete_file(file_id)
        return jsonify(result)
    except YandexClientError as e:
        return _err_response(e)


@file_bp.route("/api/files/<file_id>", methods=["GET"])
def get_file_info(file_id):
    try:
        client = get_client()
        result = client.retrieve_file(file_id)
        return jsonify(result)
    except YandexClientError as e:
        return _err_response(e)


@file_bp.route("/api/files/<file_id>/content", methods=["GET"])
def download_file(file_id):
    try:
        client = get_client()
        meta = client.retrieve_file(file_id)
        filename = meta.get("filename", file_id)
        content = client.download_file(file_id)
        return Response(
            content,
            mimetype="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except YandexClientError as e:
        return _err_response(e)


@file_bp.route("/api/vector-stores", methods=["GET"])
def list_vector_stores():
    try:
        limit = request.args.get("limit", 100, type=int)
        client = get_client()
        data = client.list_vector_stores(limit=limit)
        return jsonify(data)
    except YandexClientError as e:
        return _err_response(e)


@file_bp.route("/api/vector-stores", methods=["POST"])
def create_vector_store():
    try:
        data = request.get_json() or {}
        name = data.get("name")
        if not name:
            return jsonify({"error": "name is required"}), 400
        file_ids = data.get("file_ids")
        chunking_strategy = data.get("chunking_strategy")
        expires_after = data.get("expires_after")
        client = get_client()
        result = client.create_vector_store(
            name=name,
            file_ids=file_ids,
            chunking_strategy=chunking_strategy,
            expires_after=expires_after,
        )
        return jsonify(result), 201
    except YandexClientError as e:
        return _err_response(e)


@file_bp.route("/api/vector-stores/<vs_id>", methods=["DELETE"])
def delete_vector_store(vs_id):
    try:
        client = get_client()
        result = client.delete_vector_store(vs_id)
        return jsonify(result)
    except YandexClientError as e:
        return _err_response(e)


@file_bp.route("/api/vector-stores/<vs_id>", methods=["GET"])
def get_vector_store(vs_id):
    try:
        client = get_client()
        result = client.get_vector_store(vs_id)
        return jsonify(result)
    except YandexClientError as e:
        return _err_response(e)


@file_bp.route("/api/vector-stores/<vs_id>/files", methods=["GET"])
def list_vs_files(vs_id):
    try:
        limit = request.args.get("limit", 100, type=int)
        filter_status = request.args.get("filter", None)
        client = get_client()
        data = client.list_vs_files(vs_id, limit=limit, filter_status=filter_status)
        return jsonify(data)
    except YandexClientError as e:
        return _err_response(e)


@file_bp.route("/api/vector-stores/<vs_id>/files", methods=["POST"])
def add_file_to_vs(vs_id):
    try:
        data = request.get_json() or {}
        file_id = data.get("file_id")
        if not file_id:
            return jsonify({"error": "file_id is required"}), 400
        chunking_strategy = data.get("chunking_strategy")
        client = get_client()
        result = client.add_file_to_vs(vs_id, file_id, chunking_strategy=chunking_strategy)
        return jsonify(result), 201
    except YandexClientError as e:
        return _err_response(e)


@file_bp.route("/api/vector-stores/<vs_id>/files/<file_id>", methods=["DELETE"])
def remove_file_from_vs(vs_id, file_id):
    try:
        client = get_client()
        result = client.remove_file_from_vs(vs_id, file_id)
        return jsonify(result)
    except YandexClientError as e:
        return _err_response(e)


# Local Files API. Desktop keeps the historical path; Android can override it.
LOCAL_REPO_DIR = os.getenv("ALICE_LOCAL_REPO_DIR", "/sdcard/repo")


@file_bp.route("/api/local-files", methods=["GET"])
def list_local_files():
    try:
        subpath = request.args.get("path", "").strip("/")
        root_dir = os.path.abspath(LOCAL_REPO_DIR)
        target_dir = os.path.abspath(os.path.join(root_dir, subpath))
        try:
            inside_root = os.path.commonpath([root_dir, target_dir]) == root_dir
        except ValueError:
            inside_root = False
        if not inside_root:
            return jsonify({"error": "Недопустимый путь"}), 400
        if not os.path.exists(target_dir):
            return jsonify({"error": f"Папка не найдена: {target_dir}"}), 404
        items = []
        for entry in os.scandir(target_dir):
            items.append(
                {
                    "name": entry.name,
                    "is_dir": entry.is_dir(),
                    "size": entry.stat().st_size if entry.is_file() else 0,
                }
            )
        return jsonify({"success": True, "path": target_dir, "items": items})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
