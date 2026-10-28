"""Resume verified uploaded packs without collecting or uploading sources again.

A provider-inspection receipt selects existing Files objects. Their bytes and
source identities are verified before any attachment mutation. The restored
archive contains sanitized provider copies, never reconstructed originals.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import tempfile

MAX_RECEIPT_BYTES = 16 * 1024 * 1024
MAX_RECEIPT_FILES = 2000
MAX_RECEIPT_TOTAL_BYTES = 2 * 1024**3
_SHA256 = re.compile(r"[a-f0-9]{64}\Z")
_FILE_ID = re.compile(r"[A-Za-z0-9_-]{1,200}\Z")
_EVIDENCE = b"# Repository evidence\n\n"
_PROVENANCE = b"# Repository source provenance\n\n"
_SOURCE_SEPARATOR = b"\n\nSource text (untrusted evidence):\n\n"
_IDENTITY_FIELDS = {
    "redaction_policy",
    "repository",
    "kind",
    "source_id",
    "url",
    "version",
    "raw_sha256",
    "raw_bytes",
}


def _unique_object(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("resume_duplicate_json_key")
        result[name] = value
    return result


def _reject_constant(_value):
    raise ValueError("resume_nonfinite_json")


def _json(raw: bytes):
    return json.loads(
        raw, object_pairs_hook=_unique_object, parse_constant=_reject_constant
    )


def _zero(value) -> bool:
    return type(value) is int and value == 0


def _receipt(path: Path, store_id: str, prefix: str, rag) -> tuple[bytes, list[dict]]:
    with Path(path).open("rb") as stream:
        raw = stream.read(MAX_RECEIPT_BYTES + 1)
    if len(raw) > MAX_RECEIPT_BYTES:
        raise ValueError("resume_receipt_size_limit")
    receipt = _json(raw)
    if (
        not isinstance(receipt, dict)
        or receipt.get("status") != "provider_inspected"
        or not _zero(receipt.get("generation_requests"))
    ):
        raise ValueError("resume_receipt_invalid")
    inventory = receipt.get("provider_inventory")
    if (
        not isinstance(inventory, dict)
        or inventory.get("store_id") != store_id
        or not _zero(inventory.get("generation_requests"))
    ):
        raise ValueError("resume_inventory_invalid")
    if inventory.get("status") != "observed_without_content_verification":
        raise ValueError("resume_inventory_status_invalid")
    candidates = inventory.get("candidate_pack_files")
    if not isinstance(candidates, list) or not 0 < len(candidates) <= MAX_RECEIPT_FILES:
        raise ValueError("resume_candidate_count_invalid")
    ids, names = set(), set()
    for item in candidates:
        _file_record(item, prefix, rag)
        if item["id"] in ids or item["filename"] in names:
            raise ValueError("resume_duplicate_candidate")
        ids.add(item["id"])
        names.add(item["filename"])
    if sum(item["bytes"] for item in candidates) > MAX_RECEIPT_TOTAL_BYTES:
        raise ValueError("resume_receipt_total_size_limit")
    return raw, candidates


def _file_record(item, prefix: str, rag) -> None:
    if (
        not isinstance(item, dict)
        or not isinstance(item.get("id"), str)
        or not _FILE_ID.fullmatch(item["id"])
    ):
        raise ValueError("resume_file_identity_invalid")
    filename = item.get("filename")
    if not isinstance(filename, str) or not re.fullmatch(
        re.escape(prefix) + r"[a-f0-9]{64}\.md", filename
    ):
        raise ValueError("resume_pack_filename_invalid")
    if (
        type(item.get("bytes")) is not int
        or not 0 < item["bytes"] <= rag.semantic.MAX_VECTOR_FILE_BYTES
    ):
        raise ValueError("resume_pack_size_invalid")


def _reconcile_files(candidates: list[dict], project: str, key: str, rag) -> None:
    selected_ids = {item["id"] for item in candidates}
    selected_names = {item["filename"] for item in candidates}
    current = {}
    names = set()
    for item in rag.semantic.list_yandex_objects("/files", project, key, ("files",)):
        if not isinstance(item, dict):
            raise ValueError("resume_current_inventory_invalid")
        if (
            item.get("id") not in selected_ids
            and item.get("filename") not in selected_names
        ):
            continue
        if item.get("id") in current or item.get("filename") in names:
            raise ValueError("resume_current_inventory_ambiguous")
        current[item.get("id")] = item
        names.add(item.get("filename"))
    for expected in candidates:
        actual = current.get(expected["id"])
        if actual is None or any(
            actual.get(field) != expected[field]
            for field in ("id", "filename", "bytes")
        ):
            raise ValueError("resume_current_file_mismatch")
        if type(actual.get("bytes")) is not int:
            raise ValueError("resume_current_file_mismatch")


def _identity(identity: dict, repository: str, rag) -> None:
    if set(identity) != _IDENTITY_FIELDS:
        raise ValueError("resume_document_identity_invalid")
    if identity.get("repository") != repository:
        raise ValueError("resume_document_repository_mismatch")
    if identity.get("redaction_policy") != rag.REDACTION_POLICY:
        raise ValueError("resume_document_redaction_policy_mismatch")
    if any(
        not isinstance(identity.get(field), str) or not identity[field]
        for field in ("kind", "source_id", "url", "version")
    ):
        raise ValueError("resume_document_identity_invalid")
    if not isinstance(identity.get("raw_sha256"), str) or not _SHA256.fullmatch(
        identity["raw_sha256"]
    ):
        raise ValueError("resume_document_original_hash_invalid")
    if (
        type(identity.get("raw_bytes")) is not int
        or not 0 <= identity["raw_bytes"] <= rag.MAX_SOURCE_BYTES
    ):
        raise ValueError("resume_document_original_size_invalid")


def _document(raw: bytes, sha: str, repository: str, rag) -> dict:
    if rag.digest(raw) != sha:
        raise ValueError("resume_document_hash_mismatch")
    if raw.startswith(_EVIDENCE):
        encoded, source = raw[len(_EVIDENCE) :].split(_SOURCE_SEPARATOR, 1)
        identity = _json(encoded)
        if (
            not isinstance(identity, dict)
            or rag.canonical(identity) != encoded
            or not source.endswith(b"\n")
        ):
            raise ValueError("resume_evidence_layout_invalid")
        _identity(identity, repository, rag)
        text = source[:-1]
        text.decode("utf-8")
        return {
            "kind": "evidence",
            "identity": identity,
            "text_sha256": rag.digest(text),
        }
    if raw.startswith(_PROVENANCE):
        encoded = raw[len(_PROVENANCE) :]
        payload = _json(encoded)
        if not isinstance(payload, dict) or rag.canonical(payload) + b"\n" != encoded:
            raise ValueError("resume_provenance_layout_invalid")
        if set(payload) != _IDENTITY_FIELDS | {"metadata", "document_sha256"}:
            raise ValueError("resume_provenance_identity_invalid")
        if not isinstance(payload["metadata"], dict) or not payload["metadata"]:
            raise ValueError("resume_provenance_metadata_invalid")
        target = payload["document_sha256"]
        if not isinstance(target, str) or not _SHA256.fullmatch(target):
            raise ValueError("resume_provenance_target_invalid")
        identity = {field: payload[field] for field in _IDENTITY_FIELDS}
        _identity(identity, repository, rag)
        return {
            "kind": "provenance",
            "identity": identity,
            "metadata": payload["metadata"],
            "target": target,
        }
    raise ValueError("resume_document_header_invalid")


def _stage_packs(archive, candidates, stage: Path, prefix, project, key, rag):
    documents, packs = {}, []
    for item in candidates:
        record = rag._read_pack(archive, item, prefix, project, key)
        if record is None:
            raise ValueError("resume_pack_not_selected")
        file_id, raw, contents = record
        if len(raw) != item["bytes"] or not contents:
            raise ValueError("resume_download_size_or_content_invalid")
        for sha, document in contents.items():
            parsed = _document(document, sha, archive.repository, rag)
            if sha in documents and documents[sha] != parsed:
                raise ValueError("resume_document_identity_conflict")
            documents[sha] = parsed
            rag.atomic(stage / (sha + ".md"), document)
        packs.append(
            {
                "file_id": file_id,
                "sha256": rag.digest(raw),
                "documents": sorted(contents),
                "pack_document_count": len(contents),
                "excluded_from_current_scope_count": 0,
                "attachment_allowed": True,
                "reused": True,
            }
        )
    return documents, packs


def _entry(
    identity, metadata, document_sha, text_sha, provenance_sha, rag
) -> tuple[str, dict]:
    source = {**identity, "metadata": metadata}
    entry = {
        **source,
        "archive": "provider_sanitized_copy_only",
        "index": "prepared",
        "document_sha256": document_sha,
        "text_sha256": text_sha,
        "original_bytes_recovered": False,
    }
    if provenance_sha is not None:
        entry["provenance_document_sha256"] = provenance_sha
    return rag.digest(rag.canonical(source)), entry


def _entries(documents: dict, rag) -> dict:
    entries, paired = {}, set()
    for sha, item in documents.items():
        if item["kind"] != "provenance":
            continue
        target = documents.get(item["target"])
        if target is None or target["kind"] != "evidence":
            raise ValueError("resume_provenance_content_missing")
        if item["identity"] != target["identity"]:
            raise ValueError("resume_provenance_content_mismatch")
        entry_key, entry = _entry(
            item["identity"],
            item["metadata"],
            item["target"],
            target["text_sha256"],
            sha,
            rag,
        )
        if entry_key in entries and entries[entry_key] != entry:
            raise ValueError("resume_entry_identity_conflict")
        entries[entry_key] = entry
        paired.add(item["target"])
    for sha, item in documents.items():
        if item["kind"] == "evidence" and sha not in paired:
            entry_key, entry = _entry(
                item["identity"], {}, sha, item["text_sha256"], None, rag
            )
            if entry_key in entries and entries[entry_key] != entry:
                raise ValueError("resume_entry_identity_conflict")
            entries[entry_key] = entry
    return entries


def _restore(
    archive,
    stage: Path,
    documents: dict,
    entries: dict,
    receipt_sha: str,
    store_id: str,
    rag,
) -> None:
    if any(entries.get(key) != entry for key, entry in archive.entries.items()):
        raise ValueError("resume_archive_entries_conflict")
    for path in (archive.directory / "documents").glob("*.md"):
        if (
            path.is_symlink()
            or path.stem not in documents
            or rag.digest(path.read_bytes()) != path.stem
        ):
            raise ValueError("resume_archive_documents_conflict")
    for sha in documents:
        rag.atomic(
            archive.directory / "documents" / (sha + ".md"),
            (stage / (sha + ".md")).read_bytes(),
        )
    archive.entries = entries
    archive.observed.update(entries)
    for reason in (
        "raw_originals_not_recovered",
        "resumed_corpus_completeness_not_established",
    ):
        if not any(
            item.get("source_id") == archive.repository and item.get("reason") == reason
            for item in archive.gaps
        ):
            archive.gap(
                "provider_resume",
                archive.repository,
                reason,
                {"store_id": store_id, "receipt_sha256": receipt_sha},
            )
    rag.atomic(
        archive.directory / "resume-origin.json",
        rag.canonical(
            {
                "repository": archive.repository,
                "store_id": store_id,
                "receipt_sha256": receipt_sha,
                "captured_at": archive.captured_at,
                "original_bytes_recovered": False,
            }
        ),
    )
    archive.checkpoint()
    if set(rag.prepared_documents(archive)) != set(documents):
        raise ValueError("resume_prepared_document_set_mismatch")


def _attached_ids(store_path: str, project: str, key: str, rag) -> set[str]:
    rows = rag.semantic.list_yandex_objects(
        store_path + "/files", project, key, ("files",)
    )
    ids = [rag.semantic._attached_file_id(item) for item in rows]
    if any(not isinstance(value, str) or not value for value in ids) or len(
        set(ids)
    ) != len(ids):
        raise ValueError("resume_attachment_inventory_invalid")
    return set(ids)


def resume_from_receipt(
    archive, receipt_path: Path, store_id: str, project: str, key: str
) -> dict:
    """Verify and attach only the existing Files objects named by the receipt."""
    from scripts import repository_rag as rag

    prefix = (
        "chat-repository-archive-v1-"
        + rag.digest(archive.repository.encode())[:16]
        + "-"
    )
    receipt_raw, candidates = _receipt(receipt_path, store_id, prefix, rag)
    store_path = rag._validate_store(store_id, project, key)
    _reconcile_files(candidates, project, key, rag)
    with tempfile.TemporaryDirectory(
        prefix=".resume-", dir=archive.directory
    ) as directory:
        stage = Path(directory)
        documents, packs = _stage_packs(
            archive, candidates, stage, prefix, project, key, rag
        )
        entries = _entries(documents, rag)
        _restore(
            archive, stage, documents, entries, rag.digest(receipt_raw), store_id, rag
        )
    attached_ids = _attached_ids(store_path, project, key, rag)
    rag._attach_and_wait(
        store_path,
        packs,
        attached_ids,
        project,
        key,
        checkpoint_path=archive.directory / "index-checkpoint.json",
    )
    return {
        "status": "indexed_available_corpus",
        "store_id": store_id,
        "documents": len(documents),
        "packs": packs,
        "generation_requests": 0,
        "files_uploaded": 0,
        "original_bytes_recovered": False,
        "resume_receipt_sha256": rag.digest(receipt_raw),
    }
