"""Concrete Yandex Cloud API-key provider used by the rotation worker."""
from __future__ import annotations

from datetime import datetime
import os
from typing import Iterable, Optional

import requests


class YandexApiKeyProvider:
    """Create/revoke service-account API keys.

    A static Yandex API key is an authentication credential for Yandex Cloud
    APIs. Yandex does not document a non-billable AI Studio endpoint that can
    universally validate the key without also imposing an API-specific
    permission. Do not turn the credentials UI into a model or /models probe.
    """

    def __init__(
        self,
        *,
        iam_token: Optional[str] = None,
        service_account_id: Optional[str] = None,
        scopes: Optional[Iterable[str]] = None,
        endpoint: Optional[str] = None,
        ai_endpoint: Optional[str] = None,
        project_id: Optional[str] = None,
        timeout: float = 30.0,
    ) -> None:
        self.iam_token = iam_token or os.getenv("YANDEX_IAM_TOKEN")
        self.service_account_id = service_account_id or os.getenv("YANDEX_SERVICE_ACCOUNT_ID")
        raw_scopes = (
            list(scopes)
            if scopes is not None
            else [
                item.strip()
                for item in os.getenv(
                    "YANDEX_API_KEY_SCOPES",
                    "yc.ai.languageModels.execute,"
                    "yc.ai.speechkitStt.execute,"
                    "yc.ai.speechkitTts.execute",
                ).split(",")
                if item.strip()
            ]
        )
        self.scopes = raw_scopes
        self.endpoint = (endpoint or os.getenv(
            "YANDEX_IAM_ENDPOINT",
            "https://iam.api.cloud.yandex.net",
        )).rstrip("/")
        self.ai_endpoint = (ai_endpoint or os.getenv(
            "YANDEX_AI_ENDPOINT",
            "https://ai.api.cloud.yandex.net/v1",
        )).rstrip("/")
        self.project_id = project_id.strip() if isinstance(project_id, str) else ""
        if not self.project_id:
            raise ValueError("Yandex project_id is required")
        self.timeout = timeout

    def rotation_supported(self, provider_key_id: Optional[str]) -> bool:
        return bool(
            provider_key_id
            and self.iam_token
            and self.service_account_id
        )

    def _require_management_credentials(self) -> None:
        if not self.iam_token:
            raise RuntimeError("YANDEX_IAM_TOKEN is not configured")
        if not self.service_account_id:
            raise RuntimeError("YANDEX_SERVICE_ACCOUNT_ID is not configured")

    def _headers(self) -> dict[str, str]:
        self._require_management_credentials()
        return {
            "Authorization": f"Bearer {self.iam_token}",
            "Content-Type": "application/json",
        }

    def create_key(self, *, expires_at: datetime) -> tuple[str, str]:
        response = requests.post(
            f"{self.endpoint}/iam/v1/apiKeys",
            headers=self._headers(),
            json={
                "serviceAccountId": self.service_account_id,
                "description": "Alice Pro rotating global provider key",
                "scopes": self.scopes,
                "expiresAt": expires_at.isoformat().replace("+00:00", "Z"),
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        body = response.json()
        api_key = body.get("apiKey") or {}
        resource_id = api_key.get("id")
        secret = body.get("secret")
        if not resource_id or not secret:
            raise RuntimeError("Yandex API key response did not include id and secret")
        return str(resource_id), str(secret)

    def validate_key(self, api_key: str) -> None:
        """Validate only local presence; never make a billable/probing request."""
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("Yandex API key is empty")

    def revoke_key(self, provider_key_id: str) -> None:
        response = requests.delete(
            f"{self.endpoint}/iam/v1/apiKeys/{provider_key_id}",
            headers=self._headers(),
            timeout=self.timeout,
        )
        response.raise_for_status()
