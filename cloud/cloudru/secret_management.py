"""Cloud.ru Secret Management (SCSM) backend-only adapter.

Product docs (verified 2026-09-29 MSK, checked with the sources listed in
issue #477): https://cloud.ru/docs/scsm/ug/doc-contents ,
https://cloud.ru/docs/scsm/ug/topics/overview__limitations . Versions are
immutable once written and several versions may be active at once, so "switch"
and "rollback" are purely local operations: which version this app currently
trusts for a given purpose (see ``provider_credentials.SecretManagementRef``).
The rendered API reference is not machine-readable and this repository has no
mirrored copy yet (``docs/integrations/cloudru-docs-mirror.md`` is still
"planned"), so the request paths and status-code mapping below are the
best-known shape, matching the documented concepts. Confirm them on the first
live connection, the same caveat already recorded for Container Apps in
``docs/cloudru-container-apps.md``.

Design constraints from issue #477, enforced here rather than left to callers:

- Two separate identities. Runtime reads use
  ``CLOUDRU_SECRET_MANAGEMENT_KEY_ID``/``CLOUDRU_SECRET_MANAGEMENT_KEY_SECRET``,
  a key pair that should be granted a viewer-only Secret Management role in
  Cloud.ru IAM. This is deliberately a different pair from the admin
  ``CLOUDRU_IAM_KEY_ID``/``CLOUDRU_IAM_KEY_SECRET`` used elsewhere
  (``cloudru_iam.py``, ``provider_key_rotation.py``) for key/version
  administration, so an ordinary session/model process that only needs to
  resolve a pinned secret never holds admin rights. Bootstrapping this
  identity from plain environment variables (not from a secret this client
  itself would need to read) avoids a circular dependency.
- Only ``get_secret_value`` returns plaintext, and only to its direct caller.
  ``list_versions``/``get_secret_metadata`` never return the payload, so they
  are safe to route through the shared, trace-safe :class:`CloudRuClient`
  like any other Cloud.ru service call.
- ``get_secret_value`` is deliberately NOT routed through
  ``CloudRuClient.request`` even though that method does not currently log
  response bodies: its response body IS the secret, so it uses its own
  request path that never hands the response to a trace event, an exception
  message, or a process-global plaintext cache.
- No silent fallback. If Cloud.ru Secret Management is unreachable or a
  version is disabled/missing, callers get a typed ``CloudProviderError``;
  nothing here falls back to reading or writing an unencrypted copy of the
  value.
- This client is intentionally not a ``cloud.base.CloudProvider`` and is never
  registered in ``cloud/registry.py``, so it is structurally unreachable from
  the generic cloud MCP tools in ``cloud/tools.py``.
"""

from __future__ import annotations

import base64
import binascii
import os
import time
from typing import Any

import requests

from cloud.base import CloudProviderError
from cloud.cloudru.client import CloudRuClient
from cloudru_iam import CloudRuIamClient, CloudRuIamError
from trace_manager import get_current_trace


# Path shape follows the documented "secret has many immutable versions"
# model; unconfirmed against a live tenant (see module docstring).
SECRET_PATH = "/v1/secrets/{secret_id}"
VERSIONS_PATH = "/v1/secrets/{secret_id}/versions"
VALUE_PATH = "/v1/secrets/{secret_id}/versions/{version_id}/payload"

# HTTP status -> stable error code for predictable handling by callers.
_STATUS_CODES = {
    401: "auth_failed",
    403: "authorization_failed",
    404: "not_found",
    409: "version_disabled",
}


class CloudRuSecretManagementClient:
    """Backend-only client for Cloud.ru Secret Management.

    Callers must treat the return value of ``get_secret_value`` as a secret:
    never log it, trace it, or return it through MCP/tool output, prompts,
    exceptions, or HTTP debug output.
    """

    def __init__(
        self,
        *,
        key_id: str | None = None,
        key_secret: str | None = None,
        iam_client: CloudRuIamClient | None = None,
        timeout: float = 10.0,
        max_retries: int = 2,
        cache_ttl: float = 30.0,
        cache_max_entries: int = 32,
    ) -> None:
        if iam_client is not None:
            self.iam_client = iam_client
        else:
            # CloudRuIamClient() with no args falls back to the admin
            # CLOUDRU_IAM_KEY_ID/CLOUDRU_IAM_KEY_SECRET pair used elsewhere for
            # key/version administration. That fallback must never leak into
            # this runtime-read path, so the scoped credentials are assigned
            # directly instead of passed through the constructor - passing an
            # empty/None value there would itself trigger the same fallback.
            self.iam_client = CloudRuIamClient()
            self.iam_client.key_id = key_id or os.getenv("CLOUDRU_SECRET_MANAGEMENT_KEY_ID") or None
            self.iam_client.key_secret = (
                key_secret or os.getenv("CLOUDRU_SECRET_MANAGEMENT_KEY_SECRET") or None
            )
        self.timeout = float(timeout)
        self.max_retries = max(0, int(max_retries))
        # Deprecated compatibility knobs. Plaintext values are intentionally
        # never cached in-process; keeping these attributes avoids breaking
        # callers that still pass or inspect the old constructor options.
        self.cache_ttl = 0.0
        self.cache_max_entries = max(1, int(cache_max_entries))
        # Metadata-only calls reuse the shared trace-safe client; it never
        # sees a secret payload because the value fetch bypasses it entirely.
        self._client = CloudRuClient(
            iam_client=self.iam_client, timeout=self.timeout, api_key_auth=False
        )
    def _ensure_configured(self) -> None:
        if not (self.iam_client.key_id and self.iam_client.key_secret):
            raise CloudProviderError(
                "Cloud.ru Secret Management runtime credentials are not configured; "
                "set CLOUDRU_SECRET_MANAGEMENT_KEY_ID and CLOUDRU_SECRET_MANAGEMENT_KEY_SECRET "
                "to a viewer-scoped IAM key pair",
                code="auth_not_configured",
            )

    def invalidate_cache(self, secret_id: str | None = None) -> None:
        """Compatibility no-op: plaintext Secret Management values are never cached."""
        return None

    def get_secret_value(self, secret_id: str, version_id: str) -> str:
        """Return the plaintext payload for one pinned, immutable secret version."""
        secret_id = (secret_id or "").strip()
        version_id = (version_id or "").strip()
        if not secret_id or not version_id:
            raise ValueError("secret_id and version_id are required")
        self._ensure_configured()

        fetched_secret_value = self._fetch_value(secret_id, version_id)
        trace = get_current_trace()
        if trace is not None:
            trace.register_sensitive_value(fetched_secret_value)
        return fetched_secret_value

    def list_versions(self, secret_id: str) -> list[dict[str, Any]]:
        """Return non-secret version metadata (id/status/created_at)."""
        self._ensure_configured()
        secret_id = (secret_id or "").strip()
        if not secret_id:
            raise ValueError("secret_id is required")
        payload = self._client.request(
            "secret_management", "GET", VERSIONS_PATH.format(secret_id=secret_id)
        )
        versions = payload.get("versions") or payload.get("items") or []
        if not isinstance(versions, list) or any(not isinstance(v, dict) for v in versions):
            raise CloudProviderError(
                "Cloud.ru Secret Management versions payload is invalid",
                code="invalid_response",
            )
        return [dict(version) for version in versions]

    def get_version_status(self, secret_id: str, version_id: str) -> str:
        """Return one version's lifecycle status, never its value."""
        version_id = (version_id or "").strip()
        for version in self.list_versions(secret_id):
            candidate_id = str(version.get("id") or version.get("version_id") or "")
            if candidate_id == version_id:
                return str(version.get("status") or version.get("state") or "unknown")
        raise CloudProviderError(
            f"Cloud.ru secret version '{version_id}' not found",
            code="not_found",
            http_status=404,
        )

    def _fetch_value(self, secret_id: str, version_id: str) -> str:
        try:
            token = self.iam_client._token()  # noqa: SLF001 - client owns token exchange
        except CloudRuIamError as exc:
            raise CloudProviderError(
                f"Cloud.ru Secret Management authentication failed: {exc}",
                code="auth_failed",
            ) from exc

        path = VALUE_PATH.format(secret_id=secret_id, version_id=version_id)
        url = self._client.endpoint("secret_management") + path
        trace = get_current_trace()
        last_exc: requests.RequestException | None = None

        for _attempt in range(self.max_retries + 1):
            started = time.monotonic()
            try:
                response = requests.get(
                    url,
                    headers={"Accept": "application/json", "Authorization": f"Bearer {token}"},
                    timeout=self.timeout,
                )
            except requests.RequestException as exc:
                last_exc = exc
                self._trace_value_response(
                    trace, secret_id=secret_id, started=started, status=None, success=False
                )
                continue

            self._trace_value_response(
                trace,
                secret_id=secret_id,
                started=started,
                status=response.status_code,
                success=response.ok,
            )
            return self._parse_value_response(response, secret_id=secret_id)

        if trace:
            trace.record_error(
                "cloudru.secret_management.get_value",
                "Cloud.ru Secret Management request failed",
                error_type=type(last_exc).__name__ if last_exc else "RequestException",
            )
        raise CloudProviderError(
            "Cloud.ru Secret Management is unavailable", code="provider_unavailable"
        ) from last_exc

    @staticmethod
    def _trace_value_response(
        trace, *, secret_id: str, started: float, status: int | None, success: bool
    ) -> None:
        """Record only status/timing for the value endpoint - never its body."""
        if not trace:
            return
        trace.add_event(
            "provider_api_response",
            {
                "provider": "cloudru",
                "service": "secret_management",
                "operation": "get_secret_value",
                "secret_id": secret_id,
                "http_status": status,
                "timing_ms": round((time.monotonic() - started) * 1000, 2),
                "success": success,
            },
        )

    @staticmethod
    def _parse_value_response(response: requests.Response, *, secret_id: str) -> str:
        if not response.ok:
            code = _STATUS_CODES.get(response.status_code, "provider_http_error")
            raise CloudProviderError(
                f"Cloud.ru Secret Management request failed for secret '{secret_id}'",
                code=code,
                http_status=response.status_code,
            )
        try:
            response_body = response.json()
        except ValueError as exc:
            raise CloudProviderError(
                "Cloud.ru Secret Management returned an invalid response",
                code="invalid_response",
                http_status=response.status_code,
            ) from exc
        if not isinstance(response_body, dict):
            raise CloudProviderError(
                "Cloud.ru Secret Management returned a non-object payload",
                code="invalid_response",
                http_status=response.status_code,
            )
        # Cloud.ru v1 AccessSecretVersion returns v1SecretPayload:
        # {"data": "<base64 bytes>"}. Decode here so callers receive the actual
        # text secret, never the wire representation.
        encoded_secret_value = response_body.get("data")
        # Drop the parsed body before any further branch can raise, so a
        # captured exception frame never holds the raw secret under a
        # generic name such as "response_body".
        response_body = None
        if not isinstance(encoded_secret_value, str) or not encoded_secret_value:
            encoded_secret_value = None
            raise CloudProviderError(
                "Cloud.ru Secret Management response did not include secret payload data",
                code="invalid_response",
                http_status=response.status_code,
            )
        try:
            secret_bytes = base64.b64decode(encoded_secret_value, validate=True)
        except (binascii.Error, ValueError) as exc:
            encoded_secret_value = None
            raise CloudProviderError(
                "Cloud.ru Secret Management returned invalid base64 payload data",
                code="invalid_response",
                http_status=response.status_code,
            ) from exc
        encoded_secret_value = None
        try:
            secret_value = secret_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            secret_bytes = None
            raise CloudProviderError(
                "Cloud.ru Secret Management payload is not UTF-8 text",
                code="invalid_response",
                http_status=response.status_code,
            ) from exc
        secret_bytes = None
        return secret_value


__all__ = ["CloudRuSecretManagementClient"]
