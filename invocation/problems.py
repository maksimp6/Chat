"""RFC 9457 Problem Details responses for the Alice API.

Every error body carries ``type``, ``title`` and ``status``, plus the legacy
``error`` code as an extension member so existing clients keep working.
``detail`` is fixed, author-written text; never pass exception messages.
"""

from __future__ import annotations

import re

from flask import Response, jsonify

PROBLEM_MIMETYPE = "application/problem+json"
PROBLEM_TYPE_BASE = "https://alice.pro/problems/"

_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


_RESERVED = frozenset({"type", "title", "status", "detail", "instance", "error"})


def problem(
    status: int,
    code: str,
    title: str,
    detail: str | None = None,
    extensions: dict[str, object] | None = None,
) -> Response:
    """Build an ``application/problem+json`` response."""
    if not 400 <= status <= 599:
        raise ValueError("problem status must be a 4xx or 5xx code")
    if not _CODE.match(code):
        raise ValueError("problem code must be snake_case, at most 64 chars")
    body: dict[str, object] = {
        "type": PROBLEM_TYPE_BASE + code,
        "title": title,
        "status": status,
        "error": code,
    }
    if detail is not None:
        body["detail"] = detail
    for key, value in (extensions or {}).items():
        if key in _RESERVED:
            raise ValueError(f"extension member may not override {key!r}")
        body[key] = value
    response: Response = jsonify(body)
    response.status_code = status
    response.mimetype = PROBLEM_MIMETYPE
    return response


__all__ = ["PROBLEM_MIMETYPE", "PROBLEM_TYPE_BASE", "problem"]
