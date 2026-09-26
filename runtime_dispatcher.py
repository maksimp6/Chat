"""Authorized dispatch boundary for requests to managed runtimes.

Preview routes deliberately know nothing about runtime locations.  Keeping the
lookup, ownership check, transport and streaming lifetime here prevents a route
from accidentally bypassing runtime isolation as new runtime adapters are added.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Optional

import requests


class RuntimeDispatchError(Exception):
    """A client-safe runtime dispatch failure."""

    def __init__(self, code: str, status: int):
        super().__init__(code)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class RuntimeResponse:
    """Transport-neutral response returned by :class:`RuntimeDispatcher`."""

    body: Iterable[bytes]
    status: int
    headers: tuple[tuple[str, str], ...]


class RuntimeDispatcher:
    """Authorize and execute requests against exactly one runtime context."""

    _REQUEST_HEADERS_EXCLUDED = frozenset({"host", "content-length", "connection"})
    _RESPONSE_HEADERS_EXCLUDED = frozenset(
        {"content-length", "connection", "transfer-encoding", "content-encoding"}
    )

    def __init__(
        self,
        runtime_lookup: Callable[[str], Optional[Mapping[str, Any]]],
        *,
        transport: Any = requests,
        timeout: float = 30,
    ) -> None:
        self._runtime_lookup = runtime_lookup
        self._transport = transport
        self._timeout = timeout

    @staticmethod
    def _is_owned(runtime: Mapping[str, Any], owner_id: Optional[str]) -> bool:
        return not owner_id or runtime.get("owner_id") in (None, owner_id)

    def _resolve(self, runtime_id: str, owner_id: Optional[str]) -> Mapping[str, Any]:
        runtime = self._runtime_lookup(runtime_id)
        # Do not reveal whether a runtime exists when it belongs to another owner.
        if not runtime or not self._is_owned(runtime, owner_id):
            raise RuntimeDispatchError("environment_not_found", 404)
        if runtime.get("status") != "RUNNING" or not runtime.get("runtime_port"):
            raise RuntimeDispatchError("environment_not_running", 503)
        return runtime

    def dispatch(
        self,
        runtime_id: str,
        *,
        owner_id: Optional[str],
        method: str,
        subpath: str = "",
        query: Any = None,
        body: bytes = b"",
        headers: Mapping[str, str] | None = None,
        cookies: Mapping[str, str] | None = None,
    ) -> RuntimeResponse:
        runtime = self._resolve(runtime_id, owner_id)
        target = f"http://127.0.0.1:{int(runtime['runtime_port'])}/{subpath.lstrip('/')}"
        forwarded_headers = {
            key: value
            for key, value in (headers or {}).items()
            if key.lower() not in self._REQUEST_HEADERS_EXCLUDED
        }
        try:
            upstream = self._transport.request(
                method,
                target,
                params=query,
                data=body,
                headers=forwarded_headers,
                cookies=cookies,
                allow_redirects=False,
                stream=True,
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            # Transport details can include credentials or internal locations.
            raise RuntimeDispatchError("environment_runtime_unreachable", 502) from exc

        response_headers = tuple(
            (key, value)
            for key, value in upstream.headers.items()
            if key.lower() not in self._RESPONSE_HEADERS_EXCLUDED
        )

        def stream() -> Iterable[bytes]:
            try:
                for chunk in upstream.iter_content(chunk_size=8192):
                    if chunk:
                        yield chunk
            finally:
                upstream.close()

        return RuntimeResponse(stream(), upstream.status_code, response_headers)
