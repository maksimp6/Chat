"""Cloud.ru IAM API-key client used by administration and provider rotation.

The client exchanges a service-account key pair for a short-lived IAM token,
then calls Cloud.ru static API-key management endpoints. Runtime Foundation
Models requests use the separate Api-Key authentication handled by
cloudru_api_key_provider.py.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from typing import Any, Optional

import requests

from trace_manager import get_current_trace


API_KEYS_PATH = "/api/v1/service-accounts/credentials/api-keys"
SERVICE_ACCOUNTS_PATH = "/api/v1/service-accounts"
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

        trace = get_current_trace()
        started = datetime.now(timezone.utc)
        if trace:
            trace.add_event("provider_api_request", {
                "provider": "cloudru", "service": "iam", "operation": "authenticate",
                "method": "POST", "path": TOKEN_PATH,
            })
        try:
            response = requests.post(
                f"{self.endpoint}{TOKEN_PATH}",
                json={"keyId": self.key_id, "secret": self.key_secret},
                headers={"Accept": "application/json", "Content-Type": "application/json"},
                timeout=self.timeout,
            )
            response.raise_for_status()
            body = response.json()
            if trace:
                trace.add_event("provider_api_response", {
                    "provider": "cloudru", "service": "iam", "operation": "authenticate",
                    "method": "POST", "path": TOKEN_PATH, "http_status": response.status_code,
                    "timing_ms": round((datetime.now(timezone.utc) - started).total_seconds() * 1000, 2),
                    "success": True,
                })
        except (requests.RequestException, ValueError) as exc:
            if trace:
                trace.record_error("cloudru.iam.authenticate", "Cloud.ru IAM authentication failed", exception=exc)
                trace.add_event("provider_api_response", {
                    "provider": "cloudru", "service": "iam", "operation": "authenticate",
                    "method": "POST", "path": TOKEN_PATH,
                    "http_status": getattr(getattr(exc, "response", None), "status_code", None),
                    "timing_ms": round((datetime.now(timezone.utc) - started).total_seconds() * 1000, 2),
                    "success": False,
                })
            raise CloudRuIamError("Cloud.ru IAM authentication failed") from exc

        token = body.get("token") or body.get("access_token")
        if not token:
            raise CloudRuIamError("Cloud.ru IAM token is missing from the response")

        expires_in = int(body.get("expires_in") or body.get("expiresIn") or 3600)
        self._access_token = str(token)
        self._access_token_expires_at = now + timedelta(seconds=max(60, expires_in - 60))
        return self._access_token

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict[str, Any]] = None,
        json_body: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        trace = get_current_trace()
        started = datetime.now(timezone.utc)
        if trace:
            trace.add_event("provider_api_request", {
                "provider": "cloudru", "service": "iam", "operation": path,
                "method": method, "path": path,
                "query_keys": sorted((params or {}).keys()),
                "body_keys": sorted((json_body or {}).keys()),
            })
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
            if trace:
                trace.add_event("provider_api_response", {
                    "provider": "cloudru", "service": "iam", "operation": path,
                    "method": method, "path": path, "http_status": response.status_code,
                    "timing_ms": round((datetime.now(timezone.utc) - started).total_seconds() * 1000, 2),
                    "success": True,
                })
        except (requests.RequestException, ValueError) as exc:
            response = getattr(exc, "response", None)
            status_code = getattr(response, "status_code", None)
            if trace:
                trace.add_event("provider_api_response", {
                    "provider": "cloudru", "service": "iam", "operation": path,
                    "method": method, "path": path,
                    "http_status": status_code,
                    "timing_ms": round((datetime.now(timezone.utc) - started).total_seconds() * 1000, 2),
                    "success": False,
                })
                trace.record_error(
                    "cloudru.iam.request",
                    f"Cloud.ru IAM request failed: {method} {path}",
                    exception=exc,
                )
            suffix = f" (HTTP {status_code})" if status_code else ""
            raise CloudRuIamError(
                f"Cloud.ru IAM request failed: {method} {path}{suffix}"
            ) from exc
        if not isinstance(body, dict):
            raise CloudRuIamError("Cloud.ru IAM returned an invalid response")
        return body

    def list_service_accounts(self) -> list[dict[str, Any]]:
        body = self._request("GET", SERVICE_ACCOUNTS_PATH)
        accounts = body.get("service_accounts") or body.get("accounts") or body.get("items") or []
        return [dict(item) for item in accounts] if isinstance(accounts, list) else []

    def create_service_account(
        self,
        *,
        project_id: str,
        name: str,
        description: str = "",
    ) -> dict[str, Any]:
        if not project_id.strip():
            raise ValueError("project_id is required")
        if not name.strip():
            raise ValueError("name is required")
        body = {
            "name": name.strip(),
            "description": description.strip(),
            "target": {"project_id": project_id.strip()},
        }
        return self._request("POST", SERVICE_ACCOUNTS_PATH, json_body=body)

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

    def reissue_api_key(
        self,
        *,
        api_key_id: str,
        expires_at: Optional[str] = None,
    ) -> dict[str, Any]:
        """Reissue a static API key while retaining its resource ID."""
        if not api_key_id.strip():
            raise ValueError("api_key_id is required")
        path = f"{API_KEYS_PATH}/{api_key_id}/reissue"
        body = {"expires_at": expires_at} if expires_at else {}
        return self._request("POST", path, json_body=body)


__all__ = ["CloudRuIamError", "CloudRuIamClient"]
