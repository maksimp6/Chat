"""Flask routes of the file manager: files kept on the phone (``workspace.stored_files``).

Logos has no Yandex API (the owner deleted the Yandex project on 05.11.2026), so there are
no Yandex Files and Vector Stores any more: Logos keeps the files itself and writes each
one into its vector database. The window shows the vector database's collections with
``POST /api/vectors/collections`` (``vector_db.routes``).
"""

import logging
import os
import re
from urllib.parse import quote

from flask import Blueprint, jsonify, request, send_file

from workspace import stored_files

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
_NOT_FOUND = "Файл не найден"
_STORAGE_FAILED = "На сервере нет места или нельзя записать файл."


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
        return jsonify({"data": stored_files.listing()})
    except OSError:
        logger.exception("[FILES] Listing failed")
        return jsonify({"error": "Не удалось прочитать файлы на сервере"}), 500


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
            {
                "error": f"File type not allowed. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
            }
        ), 400
    content_length = request.content_length
    if content_length and content_length > MAX_FILE_SIZE:
        return jsonify({"error": "File too large (max 128 MB)"}), 413
    # Read at most one byte past the limit: Content-Length may be absent, so the header
    # check alone does not bound memory use.
    file_content = file.stream.read(MAX_FILE_SIZE + 1)
    if len(file_content) == 0:
        return jsonify({"error": "File is empty (0 bytes)"}), 400
    if len(file_content) > MAX_FILE_SIZE:
        return jsonify({"error": "File too large (max 128 MB)"}), 413
    try:
        result = stored_files.keep_bytes(
            file_content, file.filename, purpose=request.form.get("purpose", "assistants")
        )
    except OSError as error:
        logger.error("[FILES] Keeping '%s' failed: %s", file.filename, error)
        return jsonify({"error": _STORAGE_FAILED}), 507
    return jsonify(result), 201


@file_bp.route("/api/files/<file_id>", methods=["DELETE"])
def delete_file(file_id):
    try:
        removed = stored_files.remove(file_id)
    except OSError as error:
        logger.error("[FILES] Deleting %s failed: %s", file_id, error)
        return jsonify({"error": "Не удалось удалить файл на сервере"}), 500
    if not removed:
        return jsonify({"error": _NOT_FOUND}), 404
    return jsonify({"id": file_id, "deleted": True})


@file_bp.route("/api/files/<file_id>", methods=["GET"])
def get_file_info(file_id):
    record = stored_files.get(file_id)
    if record is None:
        return jsonify({"error": _NOT_FOUND}), 404
    return jsonify(record)


@file_bp.route("/api/files/<file_id>/content", methods=["GET"])
def download_file(file_id):
    record = stored_files.get(file_id)
    path = stored_files.data_path(file_id)
    if record is None or path is None or not path.is_file():
        return jsonify({"error": _NOT_FOUND}), 404
    # A kept file never changes, so its id is its version: a download broken on a slow link (10 kb/s) goes on with a
    # Range request from the byte it reached instead of starting over.
    response = send_file(path, mimetype="application/octet-stream", conditional=True, etag=file_id)
    response.headers["Content-Disposition"] = _content_disposition(record["filename"])
    return response


# Local Files API. Desktop keeps the historical path; Android can override it.
LOCAL_REPO_DIR = os.environ.get("LOGOS_LOCAL_REPO_DIR", "/sdcard/repo")


@file_bp.route("/api/local-files", methods=["GET"])
def list_local_files():
    try:
        subpath = request.args.get("path", "").strip("/")
        # realpath resolves symlinks, so a link inside the repo cannot list a directory outside it.
        root_dir = os.path.realpath(LOCAL_REPO_DIR)
        target_dir = os.path.realpath(os.path.join(root_dir, subpath))
        try:
            inside_root = os.path.commonpath([root_dir, target_dir]) == root_dir
        except ValueError:
            inside_root = False
        if not inside_root:
            return jsonify({"error": "Недопустимый путь"}), 400
        if not os.path.isdir(target_dir):
            return jsonify({"error": f"Папка не найдена: /{subpath}"}), 404
        items = []
        with os.scandir(target_dir) as entries:
            for entry in entries:
                # A link is listed as itself, a non-directory of size 0, so nothing about its
                # target shows. An entry whose metadata cannot be read (a vanished file, no
                # permission) is listed without its size instead of failing the whole listing.
                try:
                    is_dir = entry.is_dir(follow_symlinks=False)
                    size = (
                        entry.stat(follow_symlinks=False).st_size
                        if entry.is_file(follow_symlinks=False)
                        else 0
                    )
                except OSError:
                    is_dir, size = False, 0
                items.append({"name": entry.name, "is_dir": is_dir, "size": size})
        rel = os.path.relpath(target_dir, root_dir)
        shown = "/" if rel == "." else "/" + rel.replace(os.sep, "/")
        return jsonify({"success": True, "path": shown, "items": items})
    except Exception:
        logger.exception("[FILES] Local listing failed")
        return jsonify({"error": "Не удалось прочитать каталог"}), 500
