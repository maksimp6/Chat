"""Admin-only Cloud.ru Secret Management write client.

Runtime readers use cloud.cloudru.secret_management with viewer-scoped credentials.
This module uses the separate admin IAM identity and never logs or traces payloads.
"""

from __future__ import annotations

import base64
import os
from typing import Any

import requests

from cloud.base import CloudProviderError
from cloud.cloudru.client import CloudRuClient
from cloud.cloudru.secret_management import SECRET_PATH, VERSIONS_PATH
from cloudru_iam import CloudRuIamClient, CloudRuIamError


class CloudRuSecretManagementAdminClient:
    def __init__(
        self,
        *,
        iam_client: CloudRuIamClient | None = None,
        parent_id: str | None = None,
        timeout: float = 10.0,
    ) -> None:
        self.iam_client = iam_client or CloudRuIamClient()
        self.parent_id = (
            parent_id or os.getenv("CLOUDRU_SECRET_MANAGEMENT_PARENT_ID") or ""
        ).strip()
        self.timeout = float(timeout)
        self._client = CloudRuClient(
            iam_client=self.iam_client,
            timeout=self.timeout,
            api_key_auth=False,
        )

    def create_secret(self, *, name: str, value: str, description: str = "") -> tuple[str, str]:
        self._ensure_admin()
        if not self.parent_id:
            raise CloudProviderError(
                "Cloud.ru Secret Management parent ID is not configured",
                code="parent_not_configured",
            )
        payload = {
            "name": name,
            "description": description,
            "parent_id": self.parent_id,
            "payload": {"data": {"value": self._encoded(value)}},
        }
        response = self._request("POST", "/v1/secrets", payload)
        secret_id = str(response.get("id") or response.get("secret_id") or "").strip()
        version_id = self._version_id(response)
        if not secret_id or not version_id:
            raise CloudProviderError(
                "Cloud.ru Secret Management create response is invalid",
                code="invalid_response",
            )
        return secret_id, version_id

    def create_version(self, *, secret_id: str, value: str) -> str:
        self._ensure_admin()
        secret_id = secret_id.strip()
        if not secret_id:
            raise ValueError("secret_id is required")
        response = self._request(
            "POST",
            VERSIONS_PATH.format(secret_id=secret_id),
            {"payload": {"data": {"value": self._encoded(value)}}},
        )
        version_id = self._version_id(response)
        if not version_id:
            raise CloudProviderError(
                "Cloud.ru Secret Management version response is invalid",
                code="invalid_response",
            )
        return version_id

    def delete_secret(self, secret_id: str) -> None:
        self._ensure_admin()
        secret_id = secret_id.strip()
        if not secret_id:
            raise ValueError("secret_id is required")
        self._request("DELETE", SECRET_PATH.format(secret_id=secret_id))

    def _ensure_admin(self) -> None:
        if not (self.iam_client.key_id and self.iam_client.key_secret):
            raise CloudProviderError(
                "Cloud.ru Secret Management admin credentials are not configured",
                code="auth_not_configured",
            )

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            token = self.iam_client._token()  # noqa: SLF001 - owns admin token exchange
        except CloudRuIamError as exc:
            raise CloudProviderError(
                "Cloud.ru Secret Management admin authentication failed",
                code="auth_failed",
            ) from exc
        url = self._client.endpoint("secret_management") + path
        try:
            response = requests.request(
                method,
                url,
                headers={"Accept": "application/json", "Authorization": f"Bearer {token}"},
                json=payload,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise CloudProviderError(
                "Cloud.ru Secret Management admin request unavailable",
                code="provider_unavailable",
            ) from exc
        if not response.ok:
            code = {
                401: "auth_failed",
                403: "authorization_failed",
                404: "not_found",
                409: "conflict",
            }.get(response.status_code, "provider_http_error")
            raise CloudProviderError(
                "Cloud.ru Secret Management admin request failed",
                code=code,
                http_status=response.status_code,
            )
        if response.status_code == 204 or not response.content:
            return {}
        try:
            body = response.json()
        except ValueError as exc:
            raise CloudProviderError(
                "Cloud.ru Secret Management admin response is invalid",
                code="invalid_response",
            ) from exc
        if not isinstance(body, dict):
            raise CloudProviderError(
                "Cloud.ru Secret Management admin response is invalid",
                code="invalid_response",
            )
        return body

    @staticmethod
    def _encoded(value: str) -> str:
        if not isinstance(value, str) or not value:
            raise ValueError("secret value must be non-empty text")
        return base64.b64encode(value.encode("utf-8")).decode("ascii")

    @staticmethod
    def _version_id(payload: dict[str, Any]) -> str:
        direct = payload.get("version_id") or payload.get("versionId")
        if direct:
            return str(direct).strip()
        version = payload.get("version")
        if isinstance(version, dict):
            return str(version.get("id") or version.get("version_id") or "").strip()
        return ""


__all__ = ["CloudRuSecretManagementAdminClient"]
