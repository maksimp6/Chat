"""Cloud.ru cloud API client with trace-safe logging."""

from __future__ import annotations

from datetime import datetime, timezone
import os
from typing import Any

import requests

from cloud.base import CloudProviderError
from cloudru_iam import CloudRuIamClient
from trace_manager import get_current_trace
from trace_security import sanitize_trace_value


DEFAULT_ENDPOINTS = {
    "iam": "https://iam.api.cloud.ru",
    "foundation_models": "https://foundation-models.api.cloud.ru/v1",
    "artifact_registry": "https://ar.api.cloud.ru",
    "container_apps": "https://containers.api.cloud.ru",
}


class CloudRuClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        iam_client: CloudRuIamClient | None = None,
        timeout: float = 20.0,
        api_key_auth: bool = True,
    ) -> None:
        # Evolution control-plane APIs accept only IAM bearer tokens; callers for
        # those services pass api_key_auth=False so a Foundation Models key is ignored.
        self.api_key = (api_key or os.getenv("CLOUDRU_API_KEY")) if api_key_auth else None
        self.iam_client = iam_client
        self.timeout = float(timeout)

    @staticmethod
    def _env_name(service: str) -> str:
        return f"CLOUDRU_{service.upper()}_ENDPOINT"

    def endpoint(self, service: str) -> str:
        configured = os.getenv(self._env_name(service), "").strip()
        if configured:
            return configured.rstrip("/")
        fallback = DEFAULT_ENDPOINTS.get(service)
        if fallback:
            return fallback.rstrip("/")
        raise CloudProviderError(
            f"Cloud.ru service endpoint is not configured for '{service}'",
            code="unsupported_capability",
        )

    def _auth_header(self) -> tuple[str, str]:
        if self.api_key:
            return "Api-Key", self.api_key
        if self.iam_client is not None:
            return "Bearer", self.iam_client._token()  # noqa: SLF001 - client owns token exchange
        iam = CloudRuIamClient()
        if iam.key_id and iam.key_secret:
            return "Bearer", iam._token()  # noqa: SLF001
        raise CloudProviderError(
            "Cloud.ru authentication is not configured",
            code="auth_not_configured",
        )

    def request(
        self,
        service: str,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not path.startswith("/"):
            raise ValueError("path must start with '/'")

        scheme, secret = self._auth_header()
        base_url = self.endpoint(service)
        url = f"{base_url}{path}"

        trace = get_current_trace()
        started = datetime.now(timezone.utc)
        request_event = {
            "provider": "cloudru",
            "service": service,
            "method": method,
            "path": path,
            "query_keys": sorted((params or {}).keys()),
            "body_keys": sorted((json_body or {}).keys()),
            "auth_scheme": scheme,
        }
        if trace:
            trace.add_event("provider_api_request", sanitize_trace_value(request_event))

        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"{scheme} {secret}",
        }

        try:
            response = requests.request(
                method,
                url,
                params=params,
                json=json_body,
                headers=headers,
                timeout=self.timeout,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            elapsed_ms = round((datetime.now(timezone.utc) - started).total_seconds() * 1000, 2)
            if trace:
                trace.add_event(
                    "provider_api_response",
                    sanitize_trace_value(
                        {
                            "provider": "cloudru",
                            "service": service,
                            "method": method,
                            "path": path,
                            "http_status": status,
                            "timing_ms": elapsed_ms,
                            "success": False,
                        }
                    ),
                )
                trace.record_error(
                    "cloudru.request",
                    f"Cloud.ru API request failed: {method} {service}{path}",
                    error_type=type(exc).__name__,
                )
            code = "authorization_failed" if status in {401, 403} else "provider_http_error"
            raise CloudProviderError(
                f"Cloud.ru API request failed: {method} {service}{path}",
                code=code,
                http_status=status,
            ) from exc

        elapsed_ms = round((datetime.now(timezone.utc) - started).total_seconds() * 1000, 2)
        if trace:
            trace.add_event(
                "provider_api_response",
                sanitize_trace_value(
                    {
                        "provider": "cloudru",
                        "service": service,
                        "method": method,
                        "path": path,
                        "http_status": response.status_code,
                        "timing_ms": elapsed_ms,
                        "success": True,
                    }
                ),
            )

        if not response.content:
            return {}
        try:
            payload = response.json()
        except ValueError as exc:
            raise CloudProviderError(
                "Cloud.ru API returned invalid JSON",
                code="invalid_response",
                http_status=response.status_code,
            ) from exc
        if not isinstance(payload, dict):
            raise CloudProviderError(
                "Cloud.ru API returned non-object payload",
                code="invalid_response",
                http_status=response.status_code,
            )
        return payload
