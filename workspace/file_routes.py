"""HTTP routes for Files and Vector Stores API"""

import logging
import os
import re
from urllib.parse import quote

from webframe import Blueprint, Response, jsonify, request

from workspace.host import bound

logger = logging.getLogger("logos.workspace.file_routes")
file_bp = Blueprint("file_manager", __name__)

ALLOWED_EXTENSIONS = {
    ".pdf",
    ".docx",
    ".xlsx",
    ".csv",
    ".md",
    ".html",
    ".json",
    ".jsonl",
    ".txt",
}
MAX_FILE_SIZE = 128 * 1024 * 1024  # 128 MB


def get_client():
    """Return a request-local Yandex client instead of a process-global Session."""
    return bound.YandexResponsesClient(bound.Config)


def _err_response(e):
    code = getattr(e, "status_code", None) or (404 if "Not found" in str(e) else 500)
    if code >= 500:
        # Provider and internal error text stays in the log, not in the response; a failure the person can act on
        # (a rejected API key) says so in its own Russian text.
        logger.error("[FILES] Request failed: %s", e)
        return jsonify(
            {
                "error": getattr(e, "user_text", None)
                or "Внутренняя ошибка сервиса файлов"
            }
        ), code
    return jsonify({"error": str(e)}), code


def _attachment_name(name):
    """An ASCII filename that cannot break out of a quoted Content-Disposition header."""
    base = os.path.basename(str(name or "")) or "file"
    return re.sub(r'[^\x20-\x7e]|["\\]', "_", base)


def _content_disposition(name):
    """The ASCII fallback plus the full UTF-8 name, so non-ASCII filenames survive the download."""
    full = os.path.basename(str(name or "")) or "file"
    return (
        f'attachment; filename="{_attachment_name(full)}"; '
        f"filename*=UTF-8''{quote(full, safe='')}"
    )


@file_bp.route("/api/files", methods=["GET"])
def list_files():
    try:
        limit = request.args.get("limit", 100, type=int)
        after = request.args.get("after", None)
        client = get_client()
        data = client.list_files(limit=limit, after=after)
        return jsonify(data)
    except bound.YandexClientError as e:
        return _err_response(e)


@file_bp.route("/api/files", methods=["POST"])
def upload_file():
    if "file" not in request.files:
        return jsonify({"error": "Файл не получен. Выберите файл и повторите."}), 400
    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "Файл не выбран. Выберите файл и повторите."}), 400
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return jsonify(
            {
                "error": f"Такой тип файла не принимается. Можно: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
            }
        ), 400
    content_length = request.content_length
    if content_length and content_length > MAX_FILE_SIZE:
        return jsonify({"error": "Файл больше 128 МБ: такой файл не принимается."}), 413
    # Read at most one byte past the limit: Content-Length may be absent, so the header
    # check alone does not bound memory use.
    file_content = file.stream.read(MAX_FILE_SIZE + 1)
    if len(file_content) == 0:
        return jsonify({"error": "Файл пустой: в нём нет данных."}), 400
    if len(file_content) > MAX_FILE_SIZE:
        return jsonify({"error": "Файл больше 128 МБ: такой файл не принимается."}), 413
    try:
        client = get_client()
        purpose = request.form.get("purpose", "assistants")
        logger.info(
            f"[FILES] Uploading '{file.filename}', size: {len(file_content)} bytes to Yandex API..."
        )
        result = client.upload_file(file_content, file.filename, purpose=purpose)
        logger.info(f"[FILES] Successfully uploaded: {result.get('id')}")
        return jsonify(result), 201
    except bound.YandexClientError as e:
        logger.error(f"[FILES] Yandex API error: {e}")
        return _err_response(e)
    except Exception as e:
        logger.error(f"[FILES] Internal upload error: {e}", exc_info=True)
        return jsonify({"error": "Внутренняя ошибка сервиса файлов"}), 500


@file_bp.route("/api/files/<file_id>", methods=["DELETE"])
def delete_file(file_id):
    try:
        client = get_client()
        result = client.delete_file(file_id)
        return jsonify(result)
    except bound.YandexClientError as e:
        return _err_response(e)


@file_bp.route("/api/files/<file_id>", methods=["GET"])
def get_file_info(file_id):
    try:
        client = get_client()
        result = client.retrieve_file(file_id)
        return jsonify(result)
    except bound.YandexClientError as e:
        return _err_response(e)


@file_bp.route("/api/files/<file_id>/content", methods=["GET"])
def download_file(file_id):
    try:
        client = get_client()
        meta = client.retrieve_file(file_id)
        filename = meta.get("filename", file_id)
        content = client.download_file(file_id)
        response = Response(
            content,
            mimetype="application/octet-stream",
            headers={"Content-Disposition": _content_disposition(filename)},
        )
        # A stored file never changes, so its id is its version: a download broken on a slow link (10 kb/s) goes
        # on with a Range request from the byte it reached instead of starting over.
        response.set_etag(file_id)
        response.headers["Accept-Ranges"] = "bytes"
        return response.make_conditional(
            request, accept_ranges=True, complete_length=len(content)
        )
    except bound.YandexClientError as e:
        return _err_response(e)


@file_bp.route("/api/vector-stores", methods=["GET"])
def list_vector_stores():
    try:
        limit = request.args.get("limit", 100, type=int)
        client = get_client()
        data = client.list_vector_stores(limit=limit)
        return jsonify(data)
    except bound.YandexClientError as e:
        return _err_response(e)


@file_bp.route("/api/vector-stores", methods=["POST"])
def create_vector_store():
    try:
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify(
                {"error": "Сервер не получил данные запроса. Повторите действие."}
            ), 400
        name = data.get("name")
        if not name:
            return jsonify({"error": "Введите название."}), 400
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
    except bound.YandexClientError as e:
        return _err_response(e)


@file_bp.route("/api/vector-stores/<vs_id>", methods=["DELETE"])
def delete_vector_store(vs_id):
    try:
        client = get_client()
        result = client.delete_vector_store(vs_id)
        return jsonify(result)
    except bound.YandexClientError as e:
        return _err_response(e)


@file_bp.route("/api/vector-stores/<vs_id>", methods=["GET"])
def get_vector_store(vs_id):
    try:
        client = get_client()
        result = client.get_vector_store(vs_id)
        return jsonify(result)
    except bound.YandexClientError as e:
        return _err_response(e)


@file_bp.route("/api/vector-stores/<vs_id>/files", methods=["GET"])
def list_vs_files(vs_id):
    try:
        limit = request.args.get("limit", 100, type=int)
        filter_status = request.args.get("filter", None)
        after = request.args.get("after", None)
        client = get_client()
        data = client.list_vs_files(
            vs_id, limit=limit, filter_status=filter_status, after=after
        )
        return jsonify(data)
    except bound.YandexClientError as e:
        return _err_response(e)


@file_bp.route("/api/vector-stores/<vs_id>/files", methods=["POST"])
def add_file_to_vs(vs_id):
    try:
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify(
                {"error": "Сервер не получил данные запроса. Повторите действие."}
            ), 400
        file_id = data.get("file_id")
        if not file_id:
            return jsonify({"error": "Файл не выбран. Выберите файл и повторите."}), 400
        chunking_strategy = data.get("chunking_strategy")
        client = get_client()
        result = client.add_file_to_vs(
            vs_id, file_id, chunking_strategy=chunking_strategy
        )
        return jsonify(result), 201
    except bound.YandexClientError as e:
        return _err_response(e)


@file_bp.route("/api/vector-stores/<vs_id>/files/<file_id>", methods=["DELETE"])
def remove_file_from_vs(vs_id, file_id):
    try:
        client = get_client()
        result = client.remove_file_from_vs(vs_id, file_id)
        return jsonify(result)
    except bound.YandexClientError as e:
        return _err_response(e)
