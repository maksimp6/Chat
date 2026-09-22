"""Cloud.ru Foundation Models API-key provider.

Runtime authentication uses:
    Authorization: Api-Key <API_KEY>

Rotation uses Cloud.ru's documented API-key reissue operation through IAM
management credentials. Reissue keeps the provider key ID unchanged, so the
old resource must not be revoked after a successful reissue.
"""
from __future__ import annotations

from datetime import datetime
import os
from typing import Optional

import requests

from cloudru_iam import CloudRuIamClient, CloudRuIamError
from trace_manager import get_current_trace


class CloudRuApiKeyProvider:
    def __init__(
        self,
        *,
        base_url: Optional[str] = None,
        iam_client: Optional[CloudRuIamClient] = None,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = (
            base_url
            or os.getenv(
                "CLOUDRU_BASE_URL",
                "https://foundation-models.api.cloud.ru/v1",
            )
        ).rstrip("/")
        self.iam_client = iam_client
        self.timeout = timeout

    def validate_key(self, api_key: str) -> None:
        """Perform a real authenticated Foundation Models API request."""
        if not api_key:
            raise ValueError("Cloud.ru API key is empty")
        trace = get_current_trace()
        started = datetime.now()
        if trace:
            trace.add_event("provider_api_request", {
                "provider": "cloudru", "service": "foundation_models",
                "operation": "validate_key", "method": "GET", "path": "/models",
            })
        try:
            response = requests.get(
                f"{self.base_url}/models",
                headers={
                    "Authorization": f"Api-Key {api_key}",
                    "Accept": "application/json",
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            if trace:
                trace.add_event("provider_api_response", {
                    "provider": "cloudru", "service": "foundation_models",
                    "operation": "validate_key", "method": "GET", "path": "/models",
                    "http_status": response.status_code,
                    "timing_ms": round((datetime.now() - started).total_seconds() * 1000, 2),
                    "success": True,
                })
        except requests.RequestException as exc:
            status = getattr(exc.response, "status_code", None)
            if trace:
                trace.add_event("provider_api_response", {
                    "provider": "cloudru", "service": "foundation_models",
                    "operation": "validate_key", "method": "GET", "path": "/models",
                    "http_status": status,
                    "timing_ms": round((datetime.now() - started).total_seconds() * 1000, 2),
                    "success": False,
                })
                trace.record_error("cloudru.foundation_models.validate_key", "Cloud.ru Foundation Models health check failed", exception=exc)
            if status in (401, 403):
                raise PermissionError("Cloud.ru Foundation Models authorization failed") from exc
            raise RuntimeError("Cloud.ru Foundation Models health check failed") from exc

    def _management_client(self) -> CloudRuIamClient:
        if self.iam_client is not None:
            return self.iam_client
        client = CloudRuIamClient()
        if not client.key_id or not client.key_secret:
            raise CloudRuIamError(
                "Cloud.ru key rotation requires CLOUDRU_IAM_KEY_ID and "
                "CLOUDRU_IAM_KEY_SECRET"
            )
        return client

    def rotation_supported(self, provider_key_id: Optional[str]) -> bool:
        """Return true only when reissue can actually be performed."""
        management = self.iam_client
        return bool(
            provider_key_id
            and management
            and management.key_id
            and management.key_secret
        )

    def reissue_key(
        self,
        provider_key_id: str,
        *,
        expires_at: datetime,
    ) -> tuple[str, str]:
        if not self.rotation_supported(provider_key_id):
            raise RuntimeError(
                "Cloud.ru API-key rotation is unavailable: provider key ID and "
                "server-side IAM management credentials are required"
            )

        body = self._management_client().reissue_api_key(
            api_key_id=provider_key_id,
            expires_at=expires_at.isoformat().replace("+00:00", "Z"),
        )
        key_id = body.get("id") or body.get("key_id")
        secret = body.get("secret")
        if not key_id or not secret:
            raise RuntimeError(
                "Cloud.ru reissue response did not include key ID and Key Secret"
            )
        return str(key_id), str(secret)

    def create_key(self, *, expires_at: datetime) -> tuple[str, str]:
        raise RuntimeError(
            "Cloud.ru provider uses in-place API-key reissue; creating a "
            "replacement requires an explicit service-account provisioning flow"
        )

    def revoke_key(self, provider_key_id: str) -> None:
        raise RuntimeError(
            "Cloud.ru rotation does not revoke the reissued resource because "
            "reissue retains the same provider key ID"
        )


__all__ = ["CloudRuApiKeyProvider"]
