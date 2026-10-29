"""Logos chat and configuration records in the native durable Kristall journal.

This was ``storage.db``; that module name still works and re-exports these names.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

from storage.host import bound, current_runtime_data_root, get_memory_client

DB_PATH = os.environ.get("LOGOS_DB_PATH", "").strip()


def memory_file_path() -> Path:
    root = current_runtime_data_root()
    if root:
        return Path(root).resolve() / "logos.memory"
    explicit = os.environ.get("LOGOS_MEMORY_PATH", "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    if not DB_PATH:
        raise bound.KristallError(
            "memory path is not configured: set LOGOS_MEMORY_PATH or LOGOS_DB_PATH"
        )
    configured = Path(DB_PATH).expanduser().resolve()
    return (
        configured
        if configured.suffix == ".memory"
        else configured.with_name("logos.memory")
    )


def get_store() -> Any:
    """Logos owns the shared writer; repositories never close it individually."""

    return get_memory_client(memory_file_path())


def open_journal_store() -> None:
    """Open the process journal, so requests reuse the one shared writer."""
    get_store()


# Former name, kept for existing callers.
init_db = open_journal_store


def reset_memory_db() -> None:
    """Reset chat/config records in explicitly isolated test clients."""
    store = get_store()
    with store.transaction() as tx:
        for key in store.items(prefix="chat/"):
            tx.delete(key)
        for key in store.items(prefix="config/"):
            tx.delete(key)


def get_conversations() -> list[dict[str, Any]]:
    records = list(get_store().items(prefix="chat/conversation/").values())
    return sorted(records, key=lambda record: record["updated_at"], reverse=True)


def create_conversation(
    conv_id: str, title: str, model: str, provider_pending: bool = False
) -> None:
    now = int(time.time())
    with get_store().transaction() as tx:
        tx.set(
            "chat/conversation/" + conv_id,
            {
                "id": conv_id,
                "title": "Новый чат" if title == "Новый диалог" else title,
                "model": model,
                "created_at": now,
                "updated_at": now,
                # True until the first message creates the provider conversation.
                "provider_pending": provider_pending,
            },
        )


# WIKIFUNCTIONS-CANDIDATE WF-01: https://www.wikifunctions.org/wiki/Z10193 + Z14592.
# Заменить ядро нормализации пробелов и усечения; адаптер: Python Unicode whitespace,
# str(title or ""), fallback «Новый чат», max_length - 1, rstrip и прежнее многоточие.
# Z14592 принимает Natural N: отрицательный stop при max_length <= 0 оставить Python-срезу.
def normalize_conversation_title(title: Any, max_length: int = 80) -> str:
    value = " ".join(str(title or "").strip().split())
    if not value:
        return "Новый чат"
    return value if len(value) <= max_length else value[: max_length - 1].rstrip() + "…"


def get_conversation_title(conv_id: str, default: Any = None) -> Any:
    record = get_store().get("chat/conversation/" + conv_id)
    return record["title"] if record is not None else default


def _update_conversation(conv_id: str, changes: dict[str, Any]) -> None:
    key = "chat/conversation/" + conv_id
    with get_store().transaction() as tx:
        record = tx.get(key)
        if record is not None:
            tx.set(key, {**record, **changes, "updated_at": int(time.time())})


def update_conversation_title(
    conv_id: str, title: Any, owner_id: str | None = None
) -> None:
    """Update the title of a shared local conversation."""
    _update_conversation(conv_id, {"title": normalize_conversation_title(title)})


def maybe_update_conversation_title(conv_id: str, source_text: Any) -> Any:
    first_line = next(
        (line.strip() for line in str(source_text or "").splitlines() if line.strip()),
        "",
    )
    key = "chat/conversation/" + conv_id
    with get_store().transaction() as tx:
        record = tx.get(key)
        if record is None:
            return None
        if first_line and record["title"] in ("Новый чат", "Новый диалог"):
            record.update(
                title=normalize_conversation_title(first_line),
                updated_at=int(time.time()),
            )
            tx.set(key, record)
        return record["title"]


def update_conversation_model(conv_id: str, model: str) -> None:
    _update_conversation(conv_id, {"model": model})


def delete_conversation(conv_id: str) -> None:
    store = get_store()
    with store.transaction() as tx:
        for key, message in store.items(prefix="chat/message/").items():
            if message["conversation_id"] == conv_id:
                tx.delete(key)
        for prefix in (
            "chat/image_request/",
            "chat/message-source/",
            "chat/message-request/",
            "chat/turn_request/",
        ):
            for key in store.items(prefix=prefix + conv_id + "/"):
                tx.delete(key)
        for prefix in ("chat/conversation/", "chat/settings/", "chat/yandex/"):
            tx.delete(prefix + conv_id)
    from storage.generated_images import delete_image_files

    delete_image_files(conv_id)


def get_messages(conv_id: str) -> list[dict[str, Any]]:
    records = [
        item
        for item in get_store().items(prefix="chat/message/").values()
        if item["conversation_id"] == conv_id
    ]
    return [
        {
            "id": item["id"],
            "role": item["role"],
            "text": item["content"],
            "cost": item["cost"],
            "created_at": item["created_at"],
            "timings": item["timings"],
            "usage": item.get("usage") or {},
            "total_duration_ms": item.get("total_duration_ms"),
            "trace": item["trace"],
            "source": item.get("source"),
            "attachments": item.get("attachments") or {},
        }
        for item in sorted(records, key=lambda item: item["id"])
    ]


def _request_message_key(conv_id: str, role: str, request_id: str) -> str:
    """The index from a turn request id to the message of one role that it stored."""
    return "chat/message-request/" + conv_id + "/" + role + "/" + request_id


def add_message(
    conv_id: str,
    role: str,
    content: Any,
    cost: float | None = 0.0,
    timings: Any = None,
    usage: Any = None,
    model: Any = None,
    source: Any = None,
    trace: Any = None,
    attachments: Any = None,
    total_duration_ms: Any = None,
    deduplicate_source: bool = False,
    request_id: str | None = None,
) -> int | None:
    """Return the message ID only after its transaction has committed.

    With ``request_id`` the message carries that turn request id, and the index from the
    request id to the message is committed in the same transaction. A request re-sent
    after a restart finds the question it stored there and does not store it again.
    """
    if not isinstance(content, str):
        content = json.dumps(content, ensure_ascii=False) if content is not None else ""
    now = int(time.time())
    key = "chat/conversation/" + conv_id
    source_key = None
    if deduplicate_source:
        if not isinstance(source, str) or not source:
            raise ValueError("deduplicated messages require a source")
        source_key = (
            "chat/message-source/"
            + conv_id
            + "/"
            + hashlib.sha256(source.encode()).hexdigest()
        )
    with get_store().transaction() as tx:
        record = tx.get(key)
        if source_key is not None and tx.get(source_key) is not None:
            return None
        if record is None:
            # The conversation was deleted while the turn ran. A message written now would be
            # unreachable from any conversation, so it is dropped.
            return None
        identity = int(tx.get("chat/next-message-id") or 0) + 1
        tx.set("chat/next-message-id", identity)
        tx.set(
            "chat/message/" + str(identity),
            {
                "id": identity,
                "conversation_id": conv_id,
                "role": role,
                "content": content,
                "cost": cost,
                "created_at": now,
                "timings": timings or [],
                # The reply's tokens and duration come back with the history, so a reload shows the same details.
                "usage": usage or {},
                "total_duration_ms": total_duration_ms,
                "trace": trace or {},
                "source": source,
                "attachments": attachments or {},
                **({"request_id": request_id} if request_id is not None else {}),
            },
        )
        tx.set(key, {**record, "updated_at": now})
        if source_key is not None:
            tx.set(source_key, identity)
        if request_id is not None:
            tx.set(_request_message_key(conv_id, role, request_id), identity)
    return identity


def get_message(conv_id: str, message_id: int) -> dict[str, Any] | None:
    """Read one stored message only when it belongs to the requested conversation."""
    record = get_store().get("chat/message/" + str(message_id))
    if not isinstance(record, dict) or record.get("conversation_id") != conv_id:
        return None
    return record


def save_conv_settings(conv_id: str, settings_dict: dict[str, Any]) -> None:
    with get_store().transaction() as tx:
        tx.set("chat/settings/" + conv_id, settings_dict)


def get_conv_settings(conv_id: str) -> dict[str, Any] | None:
    return get_store().get("chat/settings/" + conv_id)


def get_yandex_conversation(conv_id: str) -> str | None:
    return get_store().get("chat/yandex/" + conv_id)


def set_yandex_conversation(conv_id: str, yandex_id: str) -> str:
    """Map a chat to its provider conversation once; a concurrent first message keeps the first id (#A-005)."""
    key = "chat/yandex/" + conv_id
    with get_store().transaction() as tx:
        stored = tx.get(key)
        if stored:
            return stored
        tx.set(key, yandex_id)
        return yandex_id


def is_provider_pending(conv_id: str) -> bool:
    record = get_store().get("chat/conversation/" + conv_id) or {}
    return bool(record.get("provider_pending"))


def init_config_table() -> None:
    init_db()


def get_config(key: str, default: Any = None) -> Any:
    record = get_store().get("config/" + key)
    if record is None:
        return default
    value = record["value"]
    # set_config marks its records "native": their value comes back exactly as it was set.
    # An unmarked record, written before that mark, keeps the former reading of JSON text.
    if isinstance(value, str) and not record.get("native"):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def set_config(key: str, value: Any) -> None:
    with get_store().transaction() as tx:
        tx.set("config/" + key, {"value": value, "native": True})


__all__ = [
    "DB_PATH",
    "add_message",
    "create_conversation",
    "delete_conversation",
    "get_config",
    "get_conv_settings",
    "get_conversation_title",
    "get_conversations",
    "get_message",
    "get_messages",
    "get_store",
    "get_yandex_conversation",
    "init_config_table",
    "init_db",
    "is_provider_pending",
    "maybe_update_conversation_title",
    "memory_file_path",
    "normalize_conversation_title",
    "open_journal_store",
    "reset_memory_db",
    "save_conv_settings",
    "set_config",
    "set_yandex_conversation",
    "update_conversation_model",
    "update_conversation_title",
]
