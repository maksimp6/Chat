"""Native MCP server records and per-conversation selection."""

from __future__ import annotations
import uuid
import time
from typing import Any
from secret_store.credential_crypto import decrypt_secret, encrypt_secret
from storage.db import get_store

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
    "require_approval": "always",
    "require_approval_tools": "",
    "require_approval_read_only": False,
    "require_approval_never_tools": "",
    "require_approval_never_read_only": False,
    "defer_loading": False,
    "config": {},
}
DEFAULT_CONFIGS = {
    "local_git": {
        "repo_path": "",
        "timeout": 15,
        "default_log_limit": 5,
        "allowed_commands": ["status", "log", "diff", "branch", "show"],
    }
}


def init_db() -> None:
    get_store()


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


def list_servers() -> list[dict[str, Any]]:
    rows = get_store().items(prefix="mcp/server/").values()
    return [
        _public(_open_fields(row))
        for row in sorted(rows, key=lambda row: row["created_at"], reverse=True)
    ]


def create_server(data: dict[str, Any]) -> str:
    sid = str(uuid.uuid4())
    record = {**_DEFAULTS, **_seal_fields(data), "id": sid, "created_at": time.time()}
    with get_store().transaction() as tx:
        tx.set("mcp/server/" + sid, record)
    return sid


def get_server(server_id: str) -> dict[str, Any] | None:
    record = get_store().get("mcp/server/" + server_id)
    return _public(_open_fields(record)) if record is not None else None


def update_server(server_id: str, data: dict[str, Any]) -> bool:
    """Return False when no server has this id, so the caller can answer 404."""
    key = "mcp/server/" + server_id
    with get_store().transaction() as tx:
        record = tx.get(key)
        if record is None:
            return False
        tx.set(
            key,
            {
                **record,
                **_seal_fields(data),
                "id": server_id,
                "created_at": record["created_at"],
            },
        )
    return True


def delete_server(server_id: str) -> None:
    with get_store().transaction() as tx:
        tx.delete("mcp/server/" + server_id)


def get_conv_mcp_settings(conv_id: str) -> dict[str, Any]:
    return get_store().get("mcp/conversation/" + conv_id) or {
        "enabled_mcp_servers": [],
        "mcp_approval": {},
    }


def save_conv_mcp_settings(
    conv_id: str, enabled_ids: list[str], approval_map: dict[str, Any]
) -> None:
    with get_store().transaction() as tx:
        tx.set(
            "mcp/conversation/" + conv_id,
            {"enabled_mcp_servers": enabled_ids, "mcp_approval": approval_map},
        )


def get_enabled_servers_for_conv(conv_id: str) -> list[dict[str, Any]]:
    enabled = set(get_conv_mcp_settings(conv_id)["enabled_mcp_servers"])
    return [server for server in list_servers() if server["id"] in enabled]


def get_server_config(server_id: str) -> dict[str, Any]:
    server = get_server(server_id)
    if server is None:
        return {}
    return {**DEFAULT_CONFIGS.get(server["connector_id"], {}), **server.get("config", {})}


def set_server_config(server_id: str, cfg: dict[str, Any]) -> None:
    update_server(server_id, {"config": cfg})
