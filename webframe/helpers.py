"""What a view returns: JSON, redirects, files, rendered templates."""

from __future__ import annotations

import mimetypes
import os
import posixpath
import time
import zlib
from html import escape
from typing import Any
from urllib.parse import quote

from webframe.datastructures import Headers
from webframe.exceptions import NotFound
from webframe.globals import current_app, request
from webframe.http import content_disposition, http_date
from webframe.wrappers import FileIterator, Response


def jsonify(*args: Any, **kwargs: Any) -> Response:
    """An ``application/json`` response for one value, several (a list) or keywords (a dict)."""
    return current_app.json.response(*args, **kwargs)


def make_response(*args: Any) -> Response:
    """The response for what a view returns: a response, a body, a tuple with status or headers."""
    if not args:
        return current_app.response_class()
    rv = args[0] if len(args) == 1 else args
    return current_app.make_response(rv)


def redirect(location: str, code: int = 302) -> Response:
    """A redirect to ``location`` with a small page naming it."""
    shown = escape(location, quote=True)
    body = (
        "<!doctype html>\n<html lang=en>\n<title>Redirecting...</title>\n"
        "<h1>Redirecting...</h1>\n<p>You should be redirected automatically to the "
        f'target URL: <a href="{shown}">{shown}</a>. If not, click the link.\n'
    )
    response = Response(body, status=code, mimetype="text/html")
    response.headers["Location"] = quote(location, safe=":/?#[]@!$&'()*+,;=%")
    return response


def safe_join(directory: str, *pathnames: str) -> str | None:
    """``directory/pathnames`` when the names stay inside ``directory``, else ``None``."""
    parts = []
    for filename in pathnames:
        if filename == "":
            continue
        if "\0" in filename or "\\" in filename:
            return None
        normalized = posixpath.normpath(filename)
        if (
            normalized.startswith(("/", "../"))
            or normalized == ".."
            or os.path.isabs(normalized)
        ):
            return None
        parts.append(normalized)
    return os.path.join(directory, *parts) if parts else directory


def send_file(
    path_or_file: Any,
    mimetype: str | None = None,
    as_attachment: bool = False,
    download_name: str | None = None,
    conditional: bool = True,
    etag: bool | str = True,
    last_modified: Any = None,
    max_age: int | None = None,
) -> Response:
    """A file (a path or an open binary file) as a streamed response.

    With a path the size, Last-Modified and an ETag come from the file; with
    ``conditional`` the response answers ``If-None-Match`` and ``Range``.
    ``max_age`` None means ``Cache-Control: no-cache``.
    """
    if isinstance(path_or_file, (str, bytes, os.PathLike)):
        path = os.fspath(path_or_file)
        if isinstance(path, bytes):
            path = os.fsdecode(path)
        if not os.path.isabs(path):
            path = os.path.join(current_app.root_path, path)
        file = None
    else:
        path = None
        file = path_or_file
    if download_name is None and path is not None:
        download_name = os.path.basename(path)
    if mimetype is None:
        guessed = mimetypes.guess_type(download_name)[0] if download_name else None
        mimetype = guessed or "application/octet-stream"
    headers = Headers()
    if download_name is not None:
        headers["Content-Disposition"] = content_disposition(
            "attachment" if as_attachment else "inline", download_name
        )
    size: int | None = None
    mtime: float | None = None
    if path is not None:
        stat = os.stat(path)
        size = stat.st_size
        mtime = stat.st_mtime
        headers["Content-Length"] = str(size)
        file = open(path, "rb")
    response = Response(
        FileIterator(file), mimetype=mimetype, headers=headers, direct_passthrough=True
    )
    if last_modified is not None:
        response.last_modified = last_modified
    elif mtime is not None:
        response.last_modified = mtime
    if max_age is None:
        response.headers["Cache-Control"] = "no-cache"
    else:
        directives = ["public"] if max_age > 0 else ["no-cache"]
        directives.append(f"max-age={int(max_age)}")
        response.headers["Cache-Control"] = ", ".join(directives)
        response.headers["Expires"] = http_date(time.time() + max_age)
    if isinstance(etag, str):
        response.set_etag(etag)
    elif etag and path is not None and mtime is not None:
        check = zlib.adler32(path.encode("utf-8", "surrogateescape")) & 0xFFFFFFFF
        response.set_etag(f"{mtime}-{size}-{check}")
    if conditional:
        try:
            response = response.make_conditional(
                request.environ, accept_ranges=True, complete_length=size
            )
        except Exception:
            response.close()
            raise
    return response


def send_from_directory(directory: str, path: str, **kwargs: Any) -> Response:
    """A file under ``directory``; a name that leaves it or names nothing is a 404."""
    full = safe_join(directory, path)
    if full is None or not os.path.isfile(full):
        raise NotFound()
    return send_file(full, **kwargs)


def render_template(template_name: str, **context: Any) -> str:
    """The template of the application's ``templates`` folder, rendered with ``context``."""
    return current_app.render_template(template_name, context)


def render_template_string(source: str, **context: Any) -> str:
    from webframe.templating import Template

    return Template(source).render(context, __json_dumps__=current_app.json.dumps)


__all__ = [
    "jsonify",
    "make_response",
    "redirect",
    "render_template",
    "render_template_string",
    "safe_join",
    "send_file",
    "send_from_directory",
]
