"""Logos chat/config repositories backed by the native durable Kristall."""

from __future__ import annotations
from core.env import env_get
import json
from pathlib import Path
import time
from typing import Any
from runtime.request_context import current_runtime_data_root
from kristall import Kristall, KristallError

DB_PATH = env_get("LOGOS_DB_PATH", "").strip()


def memory_file_path() -> Path:
    root = current_runtime_data_root()
    if root:
        return Path(root).resolve() / "logos.memory"
    explicit = env_get("LOGOS_MEMORY_PATH", "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    if not DB_PATH:
        raise KristallError(
            "memory path is not configured: set LOGOS_MEMORY_PATH or LOGOS_DB_PATH"
        )
    configured = Path(DB_PATH).expanduser().resolve()
    return configured if configured.suffix == ".memory" else configured.with_name("logos.memory")


def get_store() -> Kristall:
    """Logos owns the shared writer; repositories never close it individually."""
    from agent_memory.client import get_memory_client

    return get_memory_client(memory_file_path())


def is_memory_configured() -> bool:
    return True


def init_db() -> None:
    get_store()


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


def create_conversation(conv_id: str, title: str, model: str) -> None:
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
            },
        )


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


def update_conversation_title(conv_id: str, title: Any, owner_id: str | None = None) -> None:
    """Update the title of a shared local conversation."""
    _update_conversation(conv_id, {"title": normalize_conversation_title(title)})


def maybe_update_conversation_title(conv_id: str, source_text: Any) -> Any:
    first_line = next(
        (line.strip() for line in str(source_text or "").splitlines() if line.strip()), ""
    )
    key = "chat/conversation/" + conv_id
    with get_store().transaction() as tx:
        record = tx.get(key)
        if record is None:
            return None
        if first_line and record["title"] in ("Новый чат", "Новый диалог"):
            record.update(
                title=normalize_conversation_title(first_line), updated_at=int(time.time())
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
        for prefix in ("chat/conversation/", "chat/settings/", "chat/yandex/"):
            tx.delete(prefix + conv_id)


def get_messages(conv_id: str) -> list[dict[str, Any]]:
    records = [
        item
        for item in get_store().items(prefix="chat/message/").values()
        if item["conversation_id"] == conv_id
    ]
    return [
        {
            "role": item["role"],
            "text": item["content"],
            "cost": item["cost"],
            "created_at": item["created_at"],
            "timings": item["timings"],
            "trace": item["trace"],
            "source": item.get("source"),
        }
        for item in sorted(records, key=lambda item: item["id"])
    ]


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
) -> None:
    if not isinstance(content, str):
        content = json.dumps(content, ensure_ascii=False) if content is not None else ""
    now = int(time.time())
    key = "chat/conversation/" + conv_id
    with get_store().transaction() as tx:
        record = tx.get(key)
        if record is None:
            # The conversation was deleted while the turn ran. A message written now would be
            # unreachable from any conversation, so it is dropped.
            return
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
                "trace": trace or {},
                "source": source,
            },
        )
        tx.set(key, {**record, "updated_at": now})


def save_conv_settings(conv_id: str, settings_dict: dict[str, Any]) -> None:
    with get_store().transaction() as tx:
        tx.set("chat/settings/" + conv_id, settings_dict)


def get_conv_settings(conv_id: str) -> dict[str, Any] | None:
    return get_store().get("chat/settings/" + conv_id)


def get_yandex_conversation(conv_id: str) -> str | None:
    return get_store().get("chat/yandex/" + conv_id)


def set_yandex_conversation(conv_id: str, yandex_id: str) -> None:
    with get_store().transaction() as tx:
        tx.set("chat/yandex/" + conv_id, yandex_id)


def init_config_table() -> None:
    init_db()


def get_config(key: str, default: Any = None) -> Any:
    record = get_store().get("config/" + key)
    if record is None:
        return default
    value = record["value"]
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def set_config(key: str, value: Any) -> None:
    with get_store().transaction() as tx:
        tx.set("config/" + key, {"value": value})
