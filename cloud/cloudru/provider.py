"""Cloud.ru provider implementation for the provider-neutral cloud surface."""

from __future__ import annotations

import os
from typing import Any

from cloud.base import CloudProviderError
from cloud.cloudru.billing import parse_consumption_total
from cloud.cloudru.client import CloudRuClient
from cloud.models import normalize_resource, normalize_resources
from cloudru_iam import CloudRuIamClient


class CloudRuProvider:
    name = "cloudru"
    _ENDPOINT_ENV_BY_SERVICE = {
        "compute": "CLOUDRU_COMPUTE_ENDPOINT",
        "storage": "CLOUDRU_STORAGE_ENDPOINT",
        "network": "CLOUDRU_NETWORK_ENDPOINT",
        "database": "CLOUDRU_DATABASE_ENDPOINT",
        "kubernetes": "CLOUDRU_KUBERNETES_ENDPOINT",
        "security": "CLOUDRU_SECURITY_ENDPOINT",
        "backup": "CLOUDRU_BACKUP_ENDPOINT",
        "billing": "CLOUDRU_BILLING_ENDPOINT",
        "observability": "CLOUDRU_OBSERVABILITY_ENDPOINT",
    }

    def __init__(
        self, client: CloudRuClient | None = None, iam_client: CloudRuIamClient | None = None
    ) -> None:
        self.client = client or CloudRuClient(iam_client=iam_client)
        self.iam_client = iam_client

    @staticmethod
    def _service_path(name: str) -> str | None:
        value = os.getenv(f"CLOUDRU_{name.upper()}_PATH", "").strip()
        return value if value.startswith("/") else None

    @staticmethod
    def _service_enabled(endpoint_env: str, path_env: str | None = None) -> bool:
        endpoint = os.getenv(endpoint_env, "").strip()
        if not endpoint:
            return False
        if path_env is None:
            return True
        return bool(os.getenv(path_env, "").strip())

    @staticmethod
    def _path_enabled(service: str, path_env: str) -> bool:
        endpoint_env = CloudRuProvider._ENDPOINT_ENV_BY_SERVICE.get(service)
        if endpoint_env is None:
            return False
        return CloudRuProvider._service_enabled(endpoint_env, path_env)

    def capabilities(self) -> dict[str, Any]:
        services = {
            "iam": {
                "enabled": True,
                "operations": ["list_service_accounts", "list_api_keys"],
                "notes": "Uses documented Cloud.ru IAM API endpoints.",
            },
            "compute": {
                "enabled": self._service_enabled(
                    "CLOUDRU_COMPUTE_ENDPOINT", "CLOUDRU_COMPUTE_PATH"
                ),
                "operations": ["list", "status", "start", "stop", "reboot"],
                "notes": "Set CLOUDRU_COMPUTE_ENDPOINT and CLOUDRU_COMPUTE_PATH.",
            },
            "observability": {
                "enabled": self._service_enabled(
                    "CLOUDRU_OBSERVABILITY_ENDPOINT", "CLOUDRU_OBSERVABILITY_LOGS_PATH"
                )
                or self._service_enabled(
                    "CLOUDRU_OBSERVABILITY_ENDPOINT", "CLOUDRU_OBSERVABILITY_METRICS_PATH"
                ),
                "operations": ["logs_query", "metrics_query"],
                "notes": "Set CLOUDRU_OBSERVABILITY_* endpoint/path values.",
            },
            "backup": {
                "enabled": self._service_enabled("CLOUDRU_BACKUP_ENDPOINT", "CLOUDRU_BACKUP_PATH"),
                "operations": ["create", "list", "restore", "delete"],
                "notes": "Set CLOUDRU_BACKUP_ENDPOINT and CLOUDRU_BACKUP_PATH.",
            },
            "billing": {
                "enabled": self._service_enabled(
                    "CLOUDRU_BILLING_ENDPOINT", "CLOUDRU_BILLING_SUMMARY_PATH"
                ),
                "operations": ["costs_summary"],
                "notes": "Set CLOUDRU_BILLING_ENDPOINT and CLOUDRU_BILLING_SUMMARY_PATH.",
            },
            "storage": {
                "enabled": self._service_enabled(
                    "CLOUDRU_STORAGE_ENDPOINT", "CLOUDRU_STORAGE_PATH"
                ),
                "operations": ["list_resources"],
            },
            "network": {
                "enabled": self._service_enabled(
                    "CLOUDRU_NETWORK_ENDPOINT", "CLOUDRU_NETWORK_PATH"
                ),
                "operations": ["list_resources"],
            },
            "database": {
                "enabled": self._service_enabled(
                    "CLOUDRU_DATABASE_ENDPOINT", "CLOUDRU_DATABASE_PATH"
                ),
                "operations": ["list_resources"],
            },
            "kubernetes": {
                "enabled": self._service_enabled(
                    "CLOUDRU_KUBERNETES_ENDPOINT", "CLOUDRU_KUBERNETES_PATH"
                ),
                "operations": ["list_resources"],
            },
            "security": {
                "enabled": self._service_enabled(
                    "CLOUDRU_SECURITY_ENDPOINT", "CLOUDRU_SECURITY_PATH"
                ),
                "operations": ["list_resources"],
            },
        }
        return {
            "provider": self.name,
            "services": services,
            "auth": {
                "api_key": bool(os.getenv("CLOUDRU_API_KEY", "").strip()),
                "iam_key_pair": bool(
                    os.getenv("CLOUDRU_IAM_KEY_ID", "").strip()
                    and os.getenv("CLOUDRU_IAM_KEY_SECRET", "").strip()
                ),
            },
        }

    def list_resources(
        self,
        *,
        service: str,
        resource_type: str | None = None,
        filters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        service_name = str(service or "").strip().lower()
        if not service_name:
            raise CloudProviderError("service is required", code="validation_error")

        if service_name == "iam":
            iam = self.iam_client or CloudRuIamClient()
            requested = str(resource_type or "service_account").strip().lower()
            if requested in {"service_account", "service_accounts"}:
                items = iam.list_service_accounts()
                normalized = normalize_resources(self.name, "iam", "service_account", items)
                return {
                    "provider": self.name,
                    "service": "iam",
                    "resource_type": "service_account",
                    "resources": normalized,
                    "raw_count": len(items),
                }
            if requested in {"api_key", "api_keys"}:
                service_account_id = str((filters or {}).get("service_account_id") or "").strip()
                if not service_account_id:
                    raise CloudProviderError(
                        "service_account_id filter is required for IAM api_key inventory",
                        code="validation_error",
                    )
                enabled_filter = (filters or {}).get("enabled")
                enabled: bool | None
                if enabled_filter is None:
                    enabled = None
                elif isinstance(enabled_filter, bool):
                    enabled = enabled_filter
                elif isinstance(enabled_filter, str):
                    normalized = enabled_filter.strip().lower()
                    if normalized in {"true", "1", "yes", "on"}:
                        enabled = True
                    elif normalized in {"false", "0", "no", "off"}:
                        enabled = False
                    else:
                        raise CloudProviderError(
                            "enabled filter must be a boolean value",
                            code="validation_error",
                        )
                else:
                    raise CloudProviderError(
                        "enabled filter must be a boolean value",
                        code="validation_error",
                    )
                items = iam.list_api_keys(service_account_id=service_account_id, enabled=enabled)
                normalized = normalize_resources(self.name, "iam", "api_key", items)
                return {
                    "provider": self.name,
                    "service": "iam",
                    "resource_type": "api_key",
                    "resources": normalized,
                    "raw_count": len(items),
                }
            raise CloudProviderError(
                f"Unsupported IAM resource_type: {requested}",
                code="unsupported_operation",
            )

        endpoint_env = self._ENDPOINT_ENV_BY_SERVICE.get(service_name)
        if endpoint_env and not self._service_enabled(
            endpoint_env, f"CLOUDRU_{service_name.upper()}_PATH"
        ):
            raise CloudProviderError(
                f"Cloud.ru service '{service_name}' is not configured",
                code="unsupported_capability",
            )

        path = self._service_path(service_name)
        if not path:
            raise CloudProviderError(
                f"Cloud.ru service '{service_name}' is not configured",
                code="unsupported_capability",
            )

        payload = self.client.request(
            service_name,
            "GET",
            path,
            params=dict(filters or {}),
        )
        raw_items = payload.get("items") or payload.get("resources") or payload.get("data") or []
        if not isinstance(raw_items, list):
            raise CloudProviderError(
                f"Cloud.ru {service_name} list payload is invalid",
                code="invalid_response",
            )
        if any(not isinstance(item, dict) for item in raw_items):
            raise CloudProviderError(
                f"Cloud.ru {service_name} list payload contains non-object entries",
                code="invalid_response",
            )
        normalized_type = resource_type or service_name
        return {
            "provider": self.name,
            "service": service_name,
            "resource_type": normalized_type,
            "resources": normalize_resources(
                self.name, service_name, normalized_type, [dict(item) for item in raw_items]
            ),
            "raw_count": len(raw_items),
        }

    def get_resource(
        self, *, resource_type: str, resource_id: str, service: str | None = None
    ) -> dict[str, Any]:
        service_name = str(service or resource_type or "").strip().lower()
        if not resource_id:
            raise CloudProviderError("resource_id is required", code="validation_error")
        if service_name == "iam":
            iam = self.iam_client or CloudRuIamClient()
            requested = str(resource_type or "").strip().lower()
            if requested in {"service_account", "service_accounts"}:
                for account in iam.list_service_accounts():
                    item_id = str(
                        account.get("id") or account.get("service_account_id") or ""
                    ).strip()
                    if item_id == resource_id:
                        return {
                            "provider": self.name,
                            "service": "iam",
                            "resource": normalize_resource(
                                self.name, "iam", "service_account", account
                            ),
                        }
                raise CloudProviderError("IAM service account not found", code="not_found")
            if requested in {"api_key", "api_keys"}:
                raise CloudProviderError(
                    "IAM api_key lookup requires service_account_id context; use cloud.resources.list",
                    code="unsupported_operation",
                )
            raise CloudProviderError(
                f"Unsupported IAM resource_type: {requested}",
                code="unsupported_operation",
            )
        endpoint_env = self._ENDPOINT_ENV_BY_SERVICE.get(service_name)
        if endpoint_env and not self._service_enabled(
            endpoint_env, f"CLOUDRU_{service_name.upper()}_PATH"
        ):
            raise CloudProviderError(
                f"Cloud.ru service '{service_name}' is not configured",
                code="unsupported_capability",
            )
        base_path = self._service_path(service_name)
        if not base_path:
            raise CloudProviderError(
                f"Cloud.ru service '{service_name}' is not configured",
                code="unsupported_capability",
            )
        payload = self.client.request(service_name, "GET", f"{base_path.rstrip('/')}/{resource_id}")
        return {
            "provider": self.name,
            "service": service_name,
            "resource": normalize_resource(
                self.name,
                service_name,
                resource_type or service_name,
                payload,
            ),
        }

    def compute(
        self,
        *,
        operation: str,
        instance_id: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        op = str(operation or "").strip().lower()
        path = self._service_path("compute")
        if not path:
            raise CloudProviderError(
                "Cloud.ru compute endpoint/path is not configured",
                code="unsupported_capability",
            )

        if op == "list":
            return self.list_resources(service="compute", resource_type="instance", filters=extra)
        if op == "status":
            if not instance_id:
                raise CloudProviderError("instance_id is required", code="validation_error")
            payload = self.client.request("compute", "GET", f"{path.rstrip('/')}/{instance_id}")
            return {
                "provider": self.name,
                "operation": op,
                "resource": normalize_resource(self.name, "compute", "instance", payload),
            }
        if op in {"start", "stop", "reboot"}:
            if not instance_id:
                raise CloudProviderError("instance_id is required", code="validation_error")
            action_template = os.getenv("CLOUDRU_COMPUTE_ACTION_PATH", "").strip()
            if action_template:
                try:
                    action_path = action_template.format(instance_id=instance_id, operation=op)
                except Exception as exc:
                    raise CloudProviderError(
                        "CLOUDRU_COMPUTE_ACTION_PATH template is invalid",
                        code="validation_error",
                    ) from exc
                if not action_path.startswith("/"):
                    raise CloudProviderError(
                        "CLOUDRU_COMPUTE_ACTION_PATH must resolve to an absolute path",
                        code="validation_error",
                    )
            else:
                action_path = f"{path.rstrip('/')}/{instance_id}/{op}"
            payload = self.client.request(
                "compute",
                "POST",
                action_path,
                json_body={"instance_id": instance_id, "operation": op, **dict(extra or {})},
            )
            return {
                "provider": self.name,
                "operation": op,
                "instance_id": instance_id,
                "result": payload,
            }

        raise CloudProviderError(
            f"Unsupported compute operation: {op}", code="unsupported_operation"
        )

    def query_logs(
        self, *, query: str, service: str | None = None, limit: int = 100
    ) -> dict[str, Any]:
        path = os.getenv("CLOUDRU_OBSERVABILITY_LOGS_PATH", "").strip()
        if not path.startswith("/") or not self._path_enabled(
            "observability", "CLOUDRU_OBSERVABILITY_LOGS_PATH"
        ):
            raise CloudProviderError(
                "Cloud.ru logs endpoint/path is not configured",
                code="unsupported_capability",
            )
        payload = self.client.request(
            "observability",
            "POST",
            path,
            json_body={"query": query, "service": service, "limit": max(1, min(int(limit), 1000))},
        )
        entries = payload.get("items") or payload.get("logs") or []
        return {
            "provider": self.name,
            "query": query,
            "service": service,
            "entries": entries if isinstance(entries, list) else [],
        }

    def query_metrics(
        self,
        *,
        query: str,
        service: str | None = None,
        window: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        path = os.getenv("CLOUDRU_OBSERVABILITY_METRICS_PATH", "").strip()
        if not path.startswith("/") or not self._path_enabled(
            "observability", "CLOUDRU_OBSERVABILITY_METRICS_PATH"
        ):
            raise CloudProviderError(
                "Cloud.ru metrics endpoint/path is not configured",
                code="unsupported_capability",
            )
        payload = self.client.request(
            "observability",
            "POST",
            path,
            json_body={
                "query": query,
                "service": service,
                "window": window,
                "limit": max(1, min(int(limit), 1000)),
            },
        )
        series = payload.get("items") or payload.get("series") or []
        return {
            "provider": self.name,
            "query": query,
            "service": service,
            "window": window,
            "series": series if isinstance(series, list) else [],
        }

    def backup(
        self,
        *,
        operation: str,
        resource_id: str | None = None,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        path = os.getenv("CLOUDRU_BACKUP_PATH", "").strip()
        if not path.startswith("/") or not self._path_enabled("backup", "CLOUDRU_BACKUP_PATH"):
            raise CloudProviderError(
                "Cloud.ru backup endpoint/path is not configured",
                code="unsupported_capability",
            )
        op = str(operation or "").strip().lower()
        if op not in {"create", "list", "restore", "delete"}:
            raise CloudProviderError(
                f"Unsupported backup operation: {op}",
                code="unsupported_operation",
            )
        method = "GET" if op == "list" else "POST"
        payload = self.client.request(
            "backup",
            method,
            path,
            params={"resource_id": resource_id} if method == "GET" and resource_id else None,
            json_body={"operation": op, "resource_id": resource_id, **dict(options or {})}
            if method == "POST"
            else None,
        )
        return {
            "provider": self.name,
            "operation": op,
            "resource_id": resource_id,
            "result": payload,
        }

    def costs_summary(
        self, *, period: str | None = None, group_by: str | None = None
    ) -> dict[str, Any]:
        path = os.getenv("CLOUDRU_BILLING_SUMMARY_PATH", "").strip()
        if not path.startswith("/") or not self._path_enabled(
            "billing", "CLOUDRU_BILLING_SUMMARY_PATH"
        ):
            raise CloudProviderError(
                "Cloud.ru billing endpoint/path is not configured",
                code="unsupported_capability",
            )
        payload = self.client.request(
            "billing",
            "GET",
            path,
            params={k: v for k, v in {"period": period, "group_by": group_by}.items() if v},
        )
        totals = parse_consumption_total(payload)
        total_cost = totals["total_cost"]
        return {
            "provider": self.name,
            "period": period,
            "group_by": group_by,
            "summary": payload.get("summary")
            if isinstance(payload.get("summary"), dict)
            else payload,
            "total_cost": str(total_cost) if total_cost is not None else None,
            "currency": totals["currency"]
            or os.getenv("CLOUDRU_BILLING_CURRENCY", "RUB").strip().upper(),
        }
