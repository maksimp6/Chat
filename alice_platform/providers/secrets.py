"""Secrets provider - fetch secrets from secure storage."""

from typing import Any, Dict


def get_secret(path: str) -> str:
    """
    Fetch secret value from secure storage.

    Args:
        path: Secret path (e.g., alice/prod/oauth-client-secret)

    Returns:
        Secret value

    Raises:
        NotImplementedError: Not implemented in this slice
    """
    raise NotImplementedError("Secrets provider deferred to next slice")


def resolve_all_secrets(secrets_config: Dict[str, Any]) -> Dict[str, str]:
    """
    Resolve all secret references to actual values.

    Args:
        secrets_config: Secrets configuration dict

    Returns:
        Resolved secrets dict

    Raises:
        NotImplementedError: Not implemented in this slice
    """
    raise NotImplementedError("Secrets provider deferred to next slice")
