"""Cloud.ru IAM API-key client used by the admin wizard.

The client exchanges a service-account key pair for a short-lived IAM token,
then calls the documented Cloud.ru static API-key endpoints.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from typing import Any, Optional

import requests


API_KEYS_PATH = "/api/v1/service-accounts/credentials/api-keys"
TOKEN_PATH = "/api/v1/auth/token"


class CloudRuIamError(RuntimeError):
    """Raised when Cloud.ru IAM cannot complete an operation."""


class CloudRuIamClient:
    def __init__(
        self,
        *,
        key_id: Optional[str] = None,
        key_secret: Optional[str] = None,
        endpoint: Optional[str] = None,
        timeout: float = 20.0,
    ) -> None:
        self.endpoint = (
            endpoint or os.getenv("CLOUDRU_IAM_ENDPOINT", "https://iam.api.cloud.ru")
        ).rstrip("/")
        self.key_id = key_id or os.getenv("CLOUDRU_IAM_KEY_ID")
        self.key_secret = key_secret or os.getenv("CLOUDRU_IAM_KEY_SECRET")
        self.timeout = timeout
        self._access_token: Optional[str] = None
        self._access_token_expires_at: Optional[datetime] = None

    def _token(self) -> str:
        now = datetime.now(timezone.utc)
        if (
            self._access_token
            and self._access_token_expires_at
            and now < self._access_token_expires_at
        ):
            return self._access_token

        if not self.key_id or not self.key_secret:
            raise CloudRuIamError(
                "CLOUDRU_IAM_KEY_ID and CLOUDRU_IAM_KEY_SECRET are required"
            )

        try:
            response = requests.post(
                f"{self.endpoint}{TOKEN_PATH}",
                json={"keyId": self.key_id, "secret": self.key_secret},
                headers={"Accept": "application/json", "Content-Type": "application/json"},
                timeout=self.timeout,
            )
            response.raise_for_status()
            body = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise CloudRuIamError("Cloud.ru IAM authentication failed") from exc

        token = body.get("token") or body.get("access_token")
        if not token:
            raise CloudRuIamError("Cloud.ru IAM token is missing from the response")

        expires_in = int(body.get("expires_in") or body.get("expiresIn") or 3600)
        self._access_token = str(token)
        self._access_token_expires_at = now + timedelta(
            seconds=max(60, expires_in - 60)
        )
        return self._access_token

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict[str, Any]] = None,
        json_body: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        try:
            response = requests.request(
                method,
                f"{self.endpoint}{path}",
                params=params,
                json=json_body,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self._token()}",
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            body = response.json() if response.content else {}
        except (requests.RequestException, ValueError) as exc:
            raise CloudRuIamError(
                f"Cloud.ru IAM request failed: {method} {path}"
            ) from exc
        if not isinstance(body, dict):
            raise CloudRuIamError("Cloud.ru IAM returned an invalid response")
        return body

    def list_api_keys(
        self,
        *,
        service_account_id: str,
        enabled: Optional[bool] = None,
    ) -> list[dict[str, Any]]:
        if not service_account_id.strip():
            raise ValueError("service_account_id is required")
        params: dict[str, Any] = {
            "filter.service_account_id": service_account_id,
            "paths": "enabled,service_account_id",
        }
        if enabled is not None:
            params["filter.enabled"] = str(enabled).lower()
        body = self._request("GET", API_KEYS_PATH, params=params)
        keys = body.get("keys", [])
        return [dict(item) for item in keys] if isinstance(keys, list) else []

    def create_api_key(
        self,
        *,
        service_account_id: str,
        name: str,
        description: str = "",
        products: list[str],
        expires_at: Optional[str] = None,
        ip_addresses: Optional[list[str]] = None,
        time_slots: Optional[list[dict[str, int]]] = None,
        timezone_offset: Optional[int] = None,
    ) -> dict[str, Any]:
        if not service_account_id.strip():
            raise ValueError("service_account_id is required")
        if not name.strip():
            raise ValueError("name is required")
        products = [item.strip() for item in products if item and item.strip()]
        if not products:
            raise ValueError("At least one Cloud.ru service is required")

        restrictions: dict[str, Any] = {}
        if ip_addresses:
            restrictions["ip_addresses"] = {
                "ip_addresses": [item.strip() for item in ip_addresses if item.strip()]
            }
        if time_slots:
            time_range: dict[str, Any] = {"time_slots": time_slots}
            if timezone_offset is not None:
                time_range["timezone"] = int(timezone_offset)
            restrictions["time_range"] = time_range

        body: dict[str, Any] = {
            "name": name.strip(),
            "description": description.strip(),
            "service_account_id": service_account_id.strip(),
            "products": products,
        }
        if restrictions:
            body["restrictions"] = restrictions
        if expires_at:
            body["expires_at"] = expires_at

        return self._request("POST", API_KEYS_PATH, json_body=body)


__all__ = ["CloudRuIamError", "CloudRuIamClient"]
