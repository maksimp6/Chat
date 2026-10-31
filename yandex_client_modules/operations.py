"""Catalog REST operations for the Court: reading and creating, through the admission boundary.

An operation is open to the Court when its contract is an HTTP operation with a JSON
body, is not marked unsupported in the snapshot, uses GET or POST, and is neither a
cancel nor a delete nor an update (owner decision: the Court reads and creates, and
removes or changes nothing). The URL comes from the catalog template with every path
parameter quoted, and api.yandex_transport validates the request before it is sent.
"""

from __future__ import annotations

import base64
import re
from typing import Any
from urllib.parse import quote

from api.contracts import ContractViolation
from api.yandex_contracts import _catalog
from api.yandex_transport import validate_yandex_request, yandex_request
from yandex_client_modules.errors import YandexClientError

ALLOWED_METHODS = ("GET", "POST")
API_HOST = "api.cloud.yandex.net"
# The socket timeout of one catalog request; it bounds a dead connection, not the work of the operation.
REQUEST_TIMEOUT_SECONDS = 120
_REFUSED_NAME = re.compile(r"(?i)cancel|delete|remove|revoke|destroy|purge|update|modify|rename")
_PLACEHOLDER = re.compile(r"\{([^{}]+)\}")


class OperationRefused(ValueError):
    """The operation is not open to the Court, or its arguments do not fit its template."""


def _refusal(name: str, entry: dict[str, Any]) -> str | None:
    binding = entry.get("transport") or {}
    if binding.get("kind") != "http":
        return "not an HTTP operation"
    if binding.get("method") not in ALLOWED_METHODS:
        return "only read (GET) and create (POST) operations are open to the Court"
    if _REFUSED_NAME.search(name):
        return "cancel, delete and update operations are not open to the Court"
    if entry.get("availability") == "unsupported_in_snapshot":
        return "marked unsupported in the snapshot"
    if entry.get("body_format", "json") != "json":
        return "body is not JSON"
    return None


def list_operations() -> list[dict[str, Any]]:
    """Every operation the Court may call: name, method, title and path parameter names."""
    rows = []
    for name, entry in _catalog().items():
        if _refusal(name, entry) is not None:
            continue
        template = entry["transport"]["url_template"]
        rows.append(
            {
                "operation": name,
                "method": entry["transport"]["method"],
                "title": entry.get("title", ""),
                "path_params": sorted(set(_PLACEHOLDER.findall(template.replace("{{ api-host }}", "")))),
            }
        )
    return sorted(rows, key=lambda row: row["operation"])


def _target(name: str, path_params: dict[str, Any]) -> tuple[str, str]:
    """The method and the full URL of an admitted operation; OperationRefused otherwise."""
    entry = _catalog().get(name)
    if entry is None:
        raise OperationRefused("unknown operation: " + name)
    reason = _refusal(name, entry)
    if reason is not None:
        raise OperationRefused(f"{name}: {reason}")
    method = entry["transport"]["method"]
    template = entry["transport"]["url_template"].replace("{{ api-host }}", API_HOST)
    names = _PLACEHOLDER.findall(template)
    if sorted(set(names)) != sorted(path_params):
        raise OperationRefused(
            f"{name}: path parameters must be exactly {', '.join(sorted(set(names))) or 'none'}"
        )
    for key in names:
        value = path_params[key]
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            raise OperationRefused(f"{name}: path parameter {key} must be a string or an integer")
    url = _PLACEHOLDER.sub(lambda match: quote(str(path_params[match.group(1)]), safe=""), template)
    return method, url


class YandexOperationMixin:
    def call_operation(
        self,
        name: str,
        *,
        path_params: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
        body: Any = None,
        execution_trace=None,
    ) -> dict[str, Any]:
        """One admitted catalog operation with the deployment's provider credential.

        Returns the HTTP status and the answer: JSON as parsed data, text as text, anything
        else base64-encoded. An answer with an error status is returned, not raised."""
        from yandex_client_modules.request_mixin import (
            _resolve_global_provider_credential,
            provider_request_headers,
        )

        method, url = _target(name, dict(path_params or {}))
        matched = validate_yandex_request(method, url, json_body=body, params=query)
        if matched != name:
            raise OperationRefused(f"{name}: the request is admitted as {matched}")
        credential = _resolve_global_provider_credential(self, execution_trace)
        headers = provider_request_headers(credential)
        self._log_request(method, url, json=body)
        try:
            response = yandex_request(
                lambda target, **options: self.session.request(method, target, **options),
                method,
                url,
                json=body,
                params=query,
                headers=headers,
                timeout=REQUEST_TIMEOUT_SECONDS,
                allow_redirects=False,
            )
        except (ContractViolation, OSError, ValueError) as exc:
            raise YandexClientError(f"{name}: {type(exc).__name__}") from exc
        self._log_response(response)
        content_type = str(response.headers.get("Content-Type", "")).lower()
        if "json" in content_type:
            try:
                payload: Any = response.json()
            except ValueError:
                payload = response.text
        elif content_type.startswith("text/") or not content_type:
            payload = response.text
        else:
            payload = {"encoding": "base64", "data": base64.b64encode(response.content).decode("ascii")}
        if execution_trace is not None:
            execution_trace.add_event(
                "yandex_operation",
                {"operation": name, "method": method, "status": response.status_code},
            )
        return {"status": response.status_code, "body": payload}
