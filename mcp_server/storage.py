"""Native shared MCP server records and per-conversation selection."""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any
from urllib.parse import urlsplit

from mcp_server.host import bound, decrypt_secret, encrypt_secret, get_store

logger = logging.getLogger("logos.mcp_server.storage")

# Credentials of an MCP server. Stored encrypted when a provider credential key is configured,
# the same rule that protects provider keys.
_SEALED_FIELDS = ("authorization", "headers")

_DEFAULTS = {
    "server_url": "",
    "connector_id": "",
    "transport": "streamable",
    "server_label": "",
    "server_description": "",
    "authorization": "",
    "headers": "",
    "allowed_tools": "",
    "allowed_tools_read_only": False,
    "require_approval": "never",
    "require_approval_tools": "",
    "require_approval_read_only": False,
    "require_approval_never_tools": "",
    "require_approval_never_read_only": False,
    "defer_loading": False,
    "config": {},
}
# Fields the server owns; a request body can never set them.
_SERVER_OWNED_FIELDS = ("id", "owner_id", "created_at")
DEFAULT_CONFIGS = {
    "local_git": {
        "repo_path": "",
        "timeout": 15,
        "default_log_limit": 5,
        "allowed_commands": ["status", "log", "diff", "branch", "show"],
    }
}


def open_journal_store() -> None:
    get_store()


# Former name, kept for existing callers.
init_db = open_journal_store


def _seal_fields(data: dict[str, Any]) -> dict[str, Any]:
    sealed = dict(data)
    for field in _SEALED_FIELDS:
        value = sealed.get(field)
        if isinstance(value, str) and value:
            sealed[field] = encrypt_secret(value)
    return sealed


def _open_fields(record: dict[str, Any]) -> dict[str, Any]:
    opened = dict(record)
    for field in _SEALED_FIELDS:
        value = opened.get(field)
        if isinstance(value, str) and value:
            opened[field] = decrypt_secret(value)
    return opened


def _public(record: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in record.items() if key != "created_at"}


def _validate_server_url(value: Any) -> None:
    """The provider connects to this URL with the server's credentials, so it must be a
    plain https endpoint without embedded credentials."""
    if value in (None, ""):
        return
    if not isinstance(value, str):
        raise ValueError("server_url must be a string")
    parts = urlsplit(value.strip())
    if parts.scheme != "https" or not parts.hostname:
        raise ValueError("server_url must be an https URL with a host")
    if parts.username is not None or parts.password is not None:
        raise ValueError("server_url must not contain credentials")


def _request_fields(data: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("MCP server data must be an object")
    _validate_server_url(data.get("server_url"))
    return {
        key: value for key, value in data.items() if key not in _SERVER_OWNED_FIELDS
    }


def list_servers(*, owner_id: str) -> list[dict[str, Any]]:
    rows = get_store().items(prefix="mcp/server/").values()
    servers = []
    for row in sorted(
        (row for row in rows if isinstance(row, dict)),
        key=lambda row: row["created_at"],
        reverse=True,
    ):
        try:
            servers.append(_public(_open_fields(row)))
        except bound.CredentialCryptoError as exc:
            # One unreadable record must not hide the other servers of the list.
            logger.warning("Skipping MCP server %s: %s", row.get("id"), exc)
    return servers


def create_server(owner_id: str, data: dict[str, Any]) -> str:
    sid = str(uuid.uuid4())
    record = {
        **_DEFAULTS,
        **_seal_fields(_request_fields(data)),
        "id": sid,
        "owner_id": owner_id,
        "created_at": time.time(),
    }
    with get_store().transaction() as tx:
        tx.set("mcp/server/" + sid, record)
    return sid


RDC_SERVER = {
    "name": "RDC — Desktop Commander",
    "server_label": "rdc",
    "server_url": "https://mcp.desktopcommander.app/mcp",
    "server_description": (
        "Remote Desktop Commander: терминал, файлы и процессы подключённых устройств "
        "владельца (ПК, сервер, телефон)."
    ),
    # Sent to the provider only once the owner has put a token into authorization.
    "config": {"requires_authorization": True},
}


def ensure_rdc_server() -> None:
    """Create the RDC server record once; the owner adds its token in the MCP panel."""
    rows = get_store().items(prefix="mcp/server/").values()
    if any(
        isinstance(row, dict) and row.get("server_url") == RDC_SERVER["server_url"]
        for row in rows
    ):
        return
    create_server("", RDC_SERVER)


def get_server(server_id: str, *, owner_id: str) -> dict[str, Any] | None:
    record = get_store().get("mcp/server/" + server_id)
    if not isinstance(record, dict):
        return None
    return _public(_open_fields(record))


def update_server(server_id: str, owner_id: str, data: dict[str, Any]) -> bool:
    """Return False when the server does not exist."""
    key = "mcp/server/" + server_id
    fields = _request_fields(data)
    with get_store().transaction() as tx:
        record = tx.get(key)
        if not isinstance(record, dict):
            return False
        tx.set(
            key,
            {
                **record,
                **_seal_fields(fields),
                "id": server_id,
                "owner_id": owner_id,
                "created_at": record["created_at"],
            },
        )
    return True


def delete_server(server_id: str, owner_id: str) -> None:
    key = "mcp/server/" + server_id
    with get_store().transaction() as tx:
        if isinstance(tx.get(key), dict):
            tx.delete(key)


def get_server_config(server_id: str, *, owner_id: str) -> dict[str, Any]:
    server = get_server(server_id, owner_id=owner_id)
    if server is None:
        return {}
    return {
        **DEFAULT_CONFIGS.get(server["connector_id"], {}),
        **server.get("config", {}),
    }


def set_server_config(server_id: str, owner_id: str, cfg: dict[str, Any]) -> None:
    update_server(server_id, owner_id, {"config": cfg})
