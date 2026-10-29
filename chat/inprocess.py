"""The application's own routes, called from inside the process as the installation owner.

A worker thread (a task on the schedule, a photo task) runs a turn or a generation
the way the browser does: through the route. Admission, the invocation, the trace,
billing, memory and the stored reply are then the ones every turn gets. The call is
a WSGI call on the application object the root installed with ``install``; no
socket is opened and no network is used. ``get_current_owner_id`` reads the
installation identity, so an in-process call acts as the owner.
"""

from __future__ import annotations

import threading
from typing import Any

_state: dict[str, Any] = {"app": None}
_lock = threading.Lock()


class ApplicationNotInstalled(RuntimeError):
    """``call`` ran before the composition root installed the application."""


def install(app: Any) -> None:
    """Keep the application for in-process calls; the root calls this once at start-up."""
    with _lock:
        _state["app"] = app


def installed() -> bool:
    with _lock:
        return _state["app"] is not None


def call(
    method: str,
    path: str,
    *,
    json: Any = None,
    query: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Run one request through the application and return its status and JSON body.

    The result is ``{"status": int, "body": dict | list | None, "text": str | None}``:
    ``body`` is the decoded JSON when the route answered JSON, else ``text`` holds the
    response text. A route that answers server-sent events is never asked for them
    here: no ``Accept: text/event-stream`` is sent unless the caller passes it.
    """
    with _lock:
        app = _state["app"]
    if app is None:
        raise ApplicationNotInstalled(
            "chat.inprocess: the application was not installed; "
            "composition.web.install must run first"
        )
    with app.test_client() as client:
        response = client.open(
            path,
            method=method,
            json=json,
            query_string=query,
            headers=headers,
        )
        status = response.status_code
        body = response.get_json(silent=True)
        text = None if body is not None else response.get_data(as_text=True)
    return {"status": status, "body": body, "text": text}


__all__ = ["ApplicationNotInstalled", "call", "install", "installed"]
