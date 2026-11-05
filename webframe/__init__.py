"""webframe: the web framework of Logos, with no dependency beyond the standard library.

An ``Application`` is a WSGI callable with URL rules, blueprints, request hooks and
error handlers. In a view, ``request`` is the current request and the view returns
a ``Response``, text, a dict (JSON), a stream, or a tuple with a status and
headers. ``jsonify``, ``send_file``, ``redirect`` and ``render_template`` build
responses; ``stream_with_context`` and ``copy_current_request_context`` keep the
request available to a stream or a worker thread. ``app.run()`` serves it with the
HTTP server in ``webframe.serving``; ``app.test_client()`` calls it in-process.
"""

from __future__ import annotations

from webframe.app import Application, Blueprint
from webframe.datastructures import Accept, FileStorage, Headers, MultiDict
from webframe.exceptions import (
    BadRequest,
    Forbidden,
    HTTPError,
    InternalServerError,
    MethodNotAllowed,
    NotFound,
    Unauthorized,
    abort,
)
from webframe.globals import (
    copy_current_request_context,
    current_app,
    has_app_context,
    has_request_context,
    request,
    stream_with_context,
)
from webframe.helpers import (
    jsonify,
    make_response,
    redirect,
    render_template,
    render_template_string,
    safe_join,
    send_file,
    send_from_directory,
)
from webframe.json import JSONProvider
from webframe.templating import Markup, escape
from webframe.wrappers import Request, Response

__all__ = [
    "Accept",
    "Application",
    "BadRequest",
    "Blueprint",
    "FileStorage",
    "Forbidden",
    "HTTPError",
    "Headers",
    "InternalServerError",
    "JSONProvider",
    "Markup",
    "MethodNotAllowed",
    "MultiDict",
    "NotFound",
    "Request",
    "Response",
    "Unauthorized",
    "abort",
    "copy_current_request_context",
    "current_app",
    "escape",
    "has_app_context",
    "has_request_context",
    "jsonify",
    "make_response",
    "redirect",
    "render_template",
    "render_template_string",
    "request",
    "safe_join",
    "send_file",
    "send_from_directory",
    "stream_with_context",
]
