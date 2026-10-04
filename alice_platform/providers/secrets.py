"""Secrets provider - fetch secrets from Cloud.ru Secret Management."""

from typing import Any, Dict
import logging

from cloud.cloudru.secret_management import CloudRuSecretManagementClient
from cloud.base import CloudProviderError

logger = logging.getLogger(__name__)


def _parse_secret_path(path: str) -> tuple[str, str]:
    """
    Parse secret path to Cloud.ru secret ID and version ID.

    Path format: alice/lane/name
    Cloud.ru secret ID: alice-lane-name
    Version ID: latest (or pinned version if available)

    Args:
        path: Secret path (e.g., alice/prod/oauth-client-secret)

    Returns:
        Tuple of (secret_id, version_id)

    Raises:
        ValueError: Invalid path format
    """
    parts = path.split("/")
    if len(parts) < 3 or parts[0] != "alice":
        raise ValueError(f"Invalid secret path format: {path}")

    lane = parts[1]
    name = "-".join(parts[2:])  # Handle names with multiple parts
    secret_id = f"alice-{lane}-{name}"

    return secret_id, "latest"


def get_secret(path: str) -> str:
    """
    Fetch secret value from Cloud.ru Secret Management.

    Args:
        path: Secret path (e.g., alice/prod/oauth-client-secret)

    Returns:
        Secret value

    Raises:
        CloudProviderError: If secret cannot be retrieved
        ValueError: If path format is invalid
    """
    try:
        secret_id, version_id = _parse_secret_path(path)
        client = CloudRuSecretManagementClient()

        # Get the latest version if "latest" is requested
        if version_id == "latest":
            versions = client.list_versions(secret_id)
            if not versions:
                raise CloudProviderError(
                    f"No versions found for secret {secret_id}", code="not_found"
                )
            # Get the first active version
            for v in versions:
                status = v.get("status") or v.get("state")
                if status in ("active", "enabled", "Active"):
                    version_id = v.get("id") or v.get("version_id")
                    break
            else:
                version_id = versions[0].get("id") or versions[0].get("version_id")

        logger.info(f"Fetching secret {secret_id} (version {version_id})")
        value = client.get_secret_value(secret_id, version_id)
        return value
    except CloudProviderError:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch secret {path}: {e}")
        raise CloudProviderError(f"Failed to fetch secret {path}: {e}", code="provider_error")


def resolve_all_secrets(secrets_config: Dict[str, Any]) -> Dict[str, str]:
    """
    Resolve all secret references to actual values.

    Args:
        secrets_config: Secrets configuration dict with secret references

    Returns:
        Resolved secrets dict

    Raises:
        CloudProviderError: If any secret cannot be retrieved
    """
    resolved = {}

    def resolve_dict(d: Dict[str, Any], prefix: str = "") -> None:
        """Recursively resolve secrets in config dict."""
        for key, value in d.items():
            full_key = f"{prefix}.{key}" if prefix else key

            if isinstance(value, str):
                # Check if this is a secret reference (starts with alice/)
                if value.startswith("alice/"):
                    try:
                        resolved[full_key] = get_secret(value)
                        logger.info(f"Resolved secret: {full_key}")
                    except Exception as e:
                        logger.error(f"Failed to resolve {full_key}: {e}")
                        raise
            elif isinstance(value, dict):
                resolve_dict(value, full_key)

    try:
        resolve_dict(secrets_config)
        logger.info(f"Resolved {len(resolved)} secrets")
        return resolved
    except Exception as e:
        logger.error(f"Failed to resolve all secrets: {e}")
        raise
