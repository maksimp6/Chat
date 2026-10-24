"""Single outbound admission boundary for dedicated Yandex API callers.

The pinned registry is authoritative. Missing or incomplete contracts are errors,
never a reason to send an unchecked request. This module makes no model calls
until a caller explicitly invokes one of the transport adapters.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Callable
from email import policy
from email.parser import BytesParser
from functools import lru_cache
from typing import Any, BinaryIO, cast
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from api.contracts import ContractViolation
from api.yandex_contracts import (
    IncompleteYandexContract,
    _catalog,
    get_yandex_contract,
    validate_yandex_contract,
)


class YandexRequestBlocked(ContractViolation):
    """An outbound request was rejected before transport invocation."""


@lru_cache(maxsize=1)
def _routes() -> tuple[tuple[str, str, re.Pattern[str], str], ...]:
    routes = []
    for name, entry in _catalog().items():
        binding = entry["transport"]
        if binding["kind"] != "http":
            continue
        template = binding["url_template"].replace("{{ api-host }}", "api.cloud.yandex.net")
        parsed = urlsplit(template)
        if parsed.scheme != "https" or "{{" in template:
            continue
        fields = re.split(r"(\{[^}]+\})", parsed.path)
        pattern = "".join(
            "(?P<" + field[1:-1] + ">[^/]+)" if field.startswith("{") else re.escape(field)
            for field in fields
        )
        routes.append((binding["method"], parsed.netloc, re.compile(pattern), name))
    # Static paths, e.g. /files/batches, take precedence over /files/{id}.
    return tuple(
        sorted(
            routes,
            key=lambda row: (
                row[2].pattern.count("(?P<"),
                not row[3].startswith("yandex.runtime."),
                row[3],
            ),
        )
    )


def _file_size(file: Any) -> int:
    if isinstance(file, (bytes, bytearray, str)):
        return len(file.encode() if isinstance(file, str) else file)
    try:
        position = file.tell()
        try:
            file.seek(0, 2)
            return file.tell() - position
        finally:
            file.seek(position)
    except (AttributeError, OSError, ValueError):
        raise IncompleteYandexContract(
            "file size cannot be verified without consuming stream"
        ) from None


def _multipart(data: Any, files: Any, content_type: str) -> dict[str, Any]:
    if files is not None:
        if not isinstance(files, dict) or set(files) != {"file"} or not isinstance(data, dict):
            raise YandexRequestBlocked("invalid multipart file fields")
        file = files["file"]
        if isinstance(file, tuple):
            if len(file) not in (2, 3, 4) or not isinstance(file[0], str):
                raise YandexRequestBlocked("invalid multipart file tuple")
            file = file[1]
        size = _file_size(file)
        metadata = dict(data)
    else:
        if (
            not isinstance(data, bytes)
            or len(data) > 128 * 1024 * 1024 + 65536
            or "\r" in content_type
            or "\n" in content_type
        ):
            raise YandexRequestBlocked("invalid multipart encoding")
        message = BytesParser(policy=policy.default).parsebytes(
            b"Content-Type: "
            + content_type.encode("ascii")
            + b"\r\nMIME-Version: 1.0\r\n\r\n"
            + data
        )
        if not message.is_multipart() or message.defects:
            raise YandexRequestBlocked("invalid multipart boundary")
        metadata = {}
        size = None
        for part in message.iter_parts():
            name = part.get_param("name", header="content-disposition")
            if part.get_content_disposition() != "form-data" or not name:
                raise YandexRequestBlocked("invalid multipart disposition")
            if name == "file" and size is None:
                size = len(part.get_payload(decode=True) or b"")
            elif name != "file" and name not in metadata:
                try:
                    metadata[name] = (part.get_payload(decode=True) or b"").decode("utf-8")
                except UnicodeDecodeError:
                    raise YandexRequestBlocked("invalid multipart metadata encoding") from None
            else:
                raise YandexRequestBlocked("duplicate multipart field")
    if size is None or not 0 < size <= 128 * 1024 * 1024:
        raise YandexRequestBlocked("multipart file size is outside contract")
    if isinstance(metadata.get("expires_after"), str):
        try:
            metadata["expires_after"] = json.loads(metadata["expires_after"])
        except ValueError:
            raise YandexRequestBlocked("invalid file expiration metadata") from None
    return metadata


def _target(method: str, url: str, base_url: str | None) -> tuple[str, dict[str, str]]:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.fragment:
        raise YandexRequestBlocked("invalid Yandex API target")
    host = parsed.netloc
    path = parsed.path
    if base_url is not None:
        base = urlsplit(base_url.rstrip("/"))
        if base.scheme != "https" or host != base.netloc or not path.startswith(base.path + "/"):
            raise YandexRequestBlocked("request is outside the configured Yandex API base")
        # The configured AI Studio reverse proxy replaces the documented /v1 base.
        host = "ai.api.cloud.yandex.net"
        path = "/v1" + path[len(base.path) :]
    for verb, expected_host, pattern, name in _routes():
        if verb != method.upper() or host != expected_host:
            continue
        match = pattern.fullmatch(path)
        if match:
            values = {key: unquote(value) for key, value in match.groupdict().items()}
            if any(
                value in (".", "..") or "/" in value or "\\" in value or not value
                for value in values.values()
            ):
                raise YandexRequestBlocked("invalid Yandex API path parameter")
            return name, values
    raise IncompleteYandexContract("no documented contract for outbound Yandex API route")


def _query_schema(contract: dict[str, Any]) -> dict[str, Any]:
    ref = contract["request"].get("query")
    if ref is None:
        return {}
    return contract["schema"]["definitions"][ref.removeprefix("#/definitions/")].get(
        "properties", {}
    )


def _query_values(url: str, params: Any, properties: dict[str, Any]) -> dict[str, Any]:
    try:
        pairs = parse_qsl(urlsplit(url).query, keep_blank_values=True, strict_parsing=True)
        if params is not None:
            encoded = (
                urlencode(params, doseq=True) if not isinstance(params, (str, bytes)) else params
            )
            pairs.extend(parse_qsl(encoded, keep_blank_values=True, strict_parsing=True))
    except (ValueError, TypeError, UnicodeDecodeError):
        raise YandexRequestBlocked("invalid query encoding") from None
    grouped: dict[str, list[str]] = {}
    for name, value in pairs:
        grouped.setdefault(name, []).append(value)
    result = {}
    for name, values in grouped.items():
        spec = properties.get(name, {})
        if spec.get("type") == "array":
            result[name] = values
        elif len(values) != 1:
            raise YandexRequestBlocked("duplicate scalar query parameter")
        elif spec.get("type") == "integer" and re.fullmatch(r"-?[0-9]{1,20}", values[0]):
            result[name] = int(values[0])
        elif spec.get("type") == "boolean" and values[0] in ("true", "false"):
            result[name] = values[0] == "true"
        else:
            result[name] = values[0]
    return result


def validate_yandex_request(
    method: str,
    url: str,
    *,
    base_url: str | None = None,
    json_body: Any = None,
    params: Any = None,
    data: Any = None,
    files: Any = None,
    headers: Any = None,
) -> str:
    """Admit all documented locations, without consuming file streams or logging data."""
    name, path = _target(method, url, base_url)
    contract = get_yandex_contract(name)
    if contract["availability"] == "unsupported_in_snapshot":
        raise IncompleteYandexContract("outbound API is marked unsupported in snapshot")
    locations = contract["request"]
    query = _query_values(url, params, _query_schema(contract))
    for part, payload in (("path", path), ("query", query)):
        if part in locations:
            validate_yandex_contract(name, payload, part=part)
        elif payload:
            raise YandexRequestBlocked("undocumented request location: " + part)
    mode = contract.get("body_format", "json")
    content_type = next(
        (value for key, value in (headers or {}).items() if key.lower() == "content-type"), ""
    )
    if mode == "audio":
        if (
            json_body is not None
            or files is not None
            or not isinstance(data, bytes)
            or not 0 < len(data) <= 1024 * 1024
        ):
            raise YandexRequestBlocked("audio request must contain 1..1048576 binary bytes")
        return name
    if mode in ("multipart", "form"):
        if json_body is not None:
            raise YandexRequestBlocked("incorrect request body encoding")
        body = _multipart(data, files, content_type) if mode == "multipart" else data
        if mode == "form":
            if not isinstance(data, dict) or len(urlencode(data).encode()) > 15 * 1024:
                raise YandexRequestBlocked("form body exceeds contract or has invalid encoding")
            body = {key: str(value) for key, value in data.items()}
        validate_yandex_contract(name, body)
        return name
    if files is not None:
        raise IncompleteYandexContract("no multipart contract for this API")
    body = json_body
    has_body = json_body is not None
    if data is not None:
        if json_body is not None:
            raise YandexRequestBlocked("multiple request body encodings")
        if (
            not isinstance(data, (bytes, str))
            or content_type.split(";", 1)[0].strip().lower() != "application/json"
        ):
            raise IncompleteYandexContract("non-JSON request body has no executable contract")
        try:
            body = json.loads(data)
            has_body = True
        except (ValueError, UnicodeDecodeError):
            raise YandexRequestBlocked("request body is not valid JSON") from None
    if "body" in locations:
        validate_yandex_contract(name, body if has_body else {})
    elif has_body:
        raise YandexRequestBlocked("undocumented request body")
    return name


def yandex_request[Result](
    send: Callable[..., Result],
    method: str,
    url: str,
    *,
    base_url: str | None = None,
    **kwargs: Any,
) -> Result:
    """Requests-compatible adapter. Validate a detached payload before calling send(url)."""
    options = dict(kwargs)
    for key in ("json", "params", "data"):
        if key in options:
            options[key] = copy.deepcopy(options[key])
    validate_yandex_request(
        method,
        url,
        base_url=base_url,
        json_body=options.get("json"),
        params=options.get("params"),
        data=options.get("data"),
        files=options.get("files"),
        headers=options.get("headers"),
    )
    # A redirect must not create an unvalidated second request or forward auth.
    options["allow_redirects"] = False
    result = send(url, **options)
    status = getattr(result, "status_code", None)
    if isinstance(status, int) and 300 <= status < 400:
        raise YandexRequestBlocked("Yandex API redirect was not followed")
    return result


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        raise YandexRequestBlocked("Yandex API redirects require a separate validated request")


def yandex_urlopen(request: Request, *, timeout: float) -> BinaryIO:
    """urllib adapter with explicit redirect denial; no global monkey-patching."""
    validate_yandex_request(
        request.get_method(),
        request.full_url,
        data=request.data,
        headers=dict(request.header_items()),
    )
    return cast(BinaryIO, build_opener(_NoRedirect()).open(request, timeout=timeout))


def yandex_connection_request(
    connection: Any, host: str, method: str, path: str, *, body: Any = None, headers: Any = None
) -> None:
    """http.client adapter: validation runs before connection.request can open a socket."""
    actual_host = getattr(connection, "host", host)
    if isinstance(actual_host, str) and actual_host != host:
        raise YandexRequestBlocked("connection host differs from validated API target")
    validate_yandex_request(method, "https://" + host + path, data=body, headers=headers)
    connection.request(method, path, body=body, headers=headers or {})
