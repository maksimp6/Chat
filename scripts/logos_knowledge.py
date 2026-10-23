"""Attach a complete native Logos case to the existing project Vector Store.

This is preparation for the native dialogue, not a second retrieval pipeline.
It reuses the semantic pipeline's Files/Vector Store transport and never creates
a store, deletes an old snapshot, or starts a model generation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from urllib.parse import quote

from scripts import semantic_pipeline as semantic
from scripts.logos_context import load_context, render_context

DEFAULT_STORE_ID = "fvtvp6t2su0g5q4ft2ev"


def prepare_knowledge(
    context_file: Path,
    *,
    project: str,
    key: str,
    store_id: str = DEFAULT_STORE_ID,
) -> dict:
    """Preserve existing knowledge and add this verified, unabridged case once."""
    if not isinstance(store_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", store_id):
        raise ValueError("knowledge_store_id_invalid")
    if not all(isinstance(value, str) and value.strip() for value in (project, key)):
        raise ValueError("knowledge_provider_credentials_missing")
    packet = load_context(context_file)
    content = (
        "# Native Logos case and complete initial context\n\n" + render_context(packet) + "\n"
    ).encode("utf-8")
    if len(content) > semantic.MAX_VECTOR_FILE_BYTES:
        raise ValueError("knowledge_context_exceeds_provider_file_limit")
    digest = hashlib.sha256(content).hexdigest()
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
    attached = semantic.ensure_vector_store_files_attached(store_id, [file_id], project, key)
    semantic.wait_for_vector_store_index(store_id, file_id, project, key)
    # A completed store does not establish the target file's status. Require
    # positive file-level evidence even when a legacy waiter accepts omissions.
    records = semantic.list_yandex_objects(
        "/vector_stores/" + quote(store_id, safe="") + "/files", project, key, ("files",)
    )
    target = [
        record for record in records if (record.get("file_id") or record.get("id")) == file_id
    ]
    if len(target) != 1 or target[0].get("status") != "completed":
        raise ValueError("knowledge_target_file_index_not_completed")
    return {
        "status": "indexed",
        "vector_store_ids": [store_id],
        "file_id": file_id,
        "filename": filename,
        "context_sha256": packet["packet_sha256"],
        "document_sha256": digest,
        "checkout_sha": packet["checkout_sha"],
        "sources": len(packet["sources"]) + 1,
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
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = prepare_knowledge(
            args.context_file,
            project=os.environ.get("YANDEX_PROJECT_ID", ""),
            key=os.environ.get("YANDEX_API_KEY", ""),
            store_id=args.store_id,
        )
    except (OSError, ValueError, TypeError) as exc:
        # Do not publish arbitrary transport messages or context text. A failed
        # receipt blocks the dialogue rather than silently disabling its RAG.
        result = {"status": "failed", "error": type(exc).__name__, "generation_requests": 0}
        if re.fullmatch(r"[a-z][a-z0-9_]{1,100}", str(exc)):
            result["error_code"] = str(exc)
        if isinstance(exc, semantic.YandexHTTPError):
            result["http_status"] = exc.status
            result["provider_code"] = exc.provider_code
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("LOGOS_KNOWLEDGE=" + json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "indexed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
