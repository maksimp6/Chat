"""Attach a complete native Logos case to the existing project Vector Store.

This is preparation for the native dialogue, not a second retrieval pipeline.
It reuses the semantic pipeline's Files/Vector Store transport and never creates
a store, deletes an old snapshot, or starts a model generation.
"""

from __future__ import annotations
from core.env import env_get

import argparse
import fcntl
import hashlib
import json
import os
import re
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from urllib.parse import quote

from scripts import semantic_pipeline as semantic
from scripts.logos_context import _canonical_json, knowledge_content, load_context

DEFAULT_STORE_ID = "fvtvp6t2su0g5q4ft2ev"
CACHE_POLICY_VERSION = 1


def _scope(owner: str | None, access: str | None, project: str, store_id: str) -> dict:
    """Use declared authority, never a credential or a credential fingerprint."""
    if any(
        not isinstance(value, str) or not value.strip() or len(value) > 500
        for value in (owner, access)
    ):
        raise ValueError("knowledge_cache_scope_missing")
    return {
        "owner": owner,
        "access_scope": access,
        "project": project,
        "store_id": store_id,
    }


@contextmanager
def _preparation_lock(project: str, store_id: str) -> Iterator[None]:
    """Serialize same-host preparation; provider reads remain authoritative.

    This advisory lock prevents duplicate uploads and attachment races between
    cooperating local processes/threads. It is not a distributed lock across
    runners. No source, credential, cache receipt or archive is stored here.
    """
    directory = Path(tempfile.gettempdir()) / f"logos-knowledge-{os.getuid()}"
    directory.mkdir(mode=0o700, exist_ok=True)
    directory_fd = os.open(directory, os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        info = os.fstat(directory_fd)
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700
        ):
            raise ValueError("knowledge_lock_directory_unsafe")
        identity = hashlib.sha256(
            _canonical_json([project, store_id]).encode()
        ).hexdigest()
        fd = os.open(
            identity + ".lock",
            os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory_fd,
        )
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_nlink != 1
            ):
                raise ValueError("knowledge_lock_file_unsafe")
            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
    finally:
        os.close(directory_fd)


def prepare_knowledge(
    context_file: Path,
    *,
    project: str,
    key: str,
    store_id: str = DEFAULT_STORE_ID,
    cache_owner: str | None = None,
    cache_access_scope: str | None = None,
) -> dict:
    """Preserve existing knowledge and add this verified, unabridged case once."""
    if not isinstance(store_id, str) or not re.fullmatch(
        r"[A-Za-z0-9_-]{1,200}", store_id
    ):
        raise ValueError("knowledge_store_id_invalid")
    if not all(isinstance(value, str) and value.strip() for value in (project, key)):
        raise ValueError("knowledge_provider_credentials_missing")
    scope = _scope(cache_owner, cache_access_scope, project, store_id)
    packet = load_context(context_file)
    captured_at = packet.get("captured_at")
    if (
        not isinstance(captured_at, str)
        or not captured_at.strip()
        or len(captured_at) > 100
    ):
        raise ValueError("knowledge_context_capture_metadata_invalid")
    reusable = knowledge_content(packet)
    cache_policy = {
        "version": CACHE_POLICY_VERSION,
        "scope": scope,
        "scope_authority": "declared_by_coordinator_not_provider_verified",
        "repository": packet["repository"],
        "chunking_strategy": semantic.vector_store_chunking_strategy(),
    }
    content = (
        "# Native Logos case: complete source content, stable collection-independent view\n\n"
        + _canonical_json({"cache_policy": cache_policy, "context": reusable})
        + "\n"
    ).encode("utf-8")
    if len(content) > semantic.MAX_VECTOR_FILE_BYTES:
        raise ValueError("knowledge_context_exceeds_provider_file_limit")
    digest = hashlib.sha256(content).hexdigest()
    with _preparation_lock(project, store_id):
        result = _index_context(content, digest, project, key, store_id)
    return {
        **result,
        "context_sha256": packet["packet_sha256"],
        "captured_at": captured_at,
        "context_content_sha256": hashlib.sha256(
            _canonical_json(reusable).encode()
        ).hexdigest(),
        "cache_policy": cache_policy,
        "checkout_sha": packet["checkout_sha"],
        "sources": len(packet["sources"]) + 1,
    }


def _index_context(
    content: bytes, digest: str, project: str, key: str, store_id: str
) -> dict:
    """Verify existing provider state inside the local preparation lock."""
    store = semantic.yandex_json_request(
        "GET", "/vector_stores/" + quote(store_id, safe=""), project, key
    )
    name = store.get("name")
    if (
        store.get("id") != store_id
        or not isinstance(name, str)
        or not name.startswith(("chat-repo-", "chat-task-history-"))
        or store.get("status") in {"failed", "expired", "cancelled"}
    ):
        raise ValueError("knowledge_existing_project_store_invalid")
    filename = f"chat-task-history-context-{digest}.md"
    file_id, _metadata = semantic.ensure_yandex_file(filename, content, project, key)
    attached = semantic.ensure_vector_store_files_attached(
        store_id, [file_id], project, key
    )
    semantic.wait_for_vector_store_index(store_id, file_id, project, key)
    # A completed store does not establish the target file's status. Require
    # positive file-level evidence even when a legacy waiter accepts omissions.
    records = semantic.list_yandex_objects(
        "/vector_stores/" + quote(store_id, safe="") + "/files",
        project,
        key,
        ("files",),
    )
    target = [
        record
        for record in records
        if (record.get("file_id") or record.get("id")) == file_id
    ]
    if len(target) != 1 or target[0].get("status") != "completed":
        raise ValueError("knowledge_target_file_index_not_completed")
    return {
        "status": "indexed",
        "vector_store_ids": [store_id],
        "file_id": file_id,
        "filename": filename,
        "document_sha256": digest,
        "bytes": len(content),
        "attached_files": attached,
        "scope": "existing_project_knowledge_plus_complete_current_case",
        "generation_requests": 0,
        "cost": {"status": "unknown", "provider_confirmed": False},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context-file", type=Path, required=True)
    parser.add_argument("--store-id", default=DEFAULT_STORE_ID)
    parser.add_argument("--cache-owner")
    parser.add_argument("--cache-access-scope")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = prepare_knowledge(
            args.context_file,
            project=env_get("YANDEX_PROJECT_ID", ""),
            key=env_get("YANDEX_API_KEY", ""),
            store_id=args.store_id,
            cache_owner=args.cache_owner,
            cache_access_scope=args.cache_access_scope,
        )
    except (OSError, ValueError, TypeError) as exc:
        # Do not publish arbitrary transport messages or context text. A failed
        # receipt blocks the dialogue rather than silently disabling its RAG.
        result = {
            "status": "failed",
            "error": type(exc).__name__,
            "generation_requests": 0,
        }
        if re.fullmatch(r"[a-z][a-z0-9_]{1,100}", str(exc)):
            result["error_code"] = str(exc)
        if isinstance(exc, semantic.YandexHTTPError):
            result["http_status"] = exc.status
            result["provider_code"] = exc.provider_code
    args.out.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("LOGOS_KNOWLEDGE=" + json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "indexed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
