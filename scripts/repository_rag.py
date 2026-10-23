"""Collect repository evidence and attach immutable packs to existing Yandex RAG.

No model generation, dialogue, Logos execution, store creation, deletion, or issue
publication is available here. The collectors report gaps instead of silently
equating the available subset with the complete repository.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import io
import json
import os
import re
import time
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from scripts import semantic_pipeline as semantic

SCHEMA = 1
REDACTION_POLICY = "repository-redaction-v2"
DEFAULT_STORE_ID = "fvtvp6t2su0g5q4ft2ev"
MAX_SOURCE_BYTES = 128 * 1024 * 1024
PACK_BYTES = 4 * 1024 * 1024
INDEX_MAX_IN_FLIGHT = 8
INDEX_TIMEOUT_SECONDS = 2 * 60 * 60
EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![\w.-])")
PACK_HEADER = "<!-- repository-rag-pack-v1 "
JSON_SECRET = re.compile(
    r'(?i)(["\'](?:password|api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|'
    r'private[_-]?key|secret[_-]?key|yandex_api_key|aws_secret_access_key|token|secret|credential)["\']\s*:\s*)'
    r'(?P<quote>["\'])(?P<value>[^"\'\r\n]{8,})(?P=quote)'
)
AUTH_SECRET = re.compile(
    r"(?i)(\bauthorization\s*[\"']?\s*[:=]\s*[\"']?\s*(?:api-key|basic|bearer)\s+)[A-Za-z0-9._~+/=-]{8,}"
)


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def atomic(path: Path, raw: bytes) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(raw)
    temporary.chmod(0o600)
    temporary.replace(path)


def safe_text(text: str, _depth: int = 0) -> tuple[str, int]:
    if _depth > 20:
        raise ValueError("redaction_nesting_limit")
    # Issue bodies and logs are often JSON-encoded strings containing another
    # JSON document or quoted assignment. Decode before masking; escaped quotes
    # must not turn an otherwise recognized secret into an uploadable value.
    if text.lstrip().startswith(("{", "[")):
        try:
            structured = json.loads(text)
        except (ValueError, RecursionError):
            structured = None
        if isinstance(structured, (dict, list)):
            count = 0

            def visit(value):
                nonlocal count
                if isinstance(value, str):
                    masked, changes = safe_text(value, _depth + 1)
                    count += changes
                    return masked
                if isinstance(value, list):
                    return [visit(item) for item in value]
                if isinstance(value, dict):
                    return {key: visit(item) for key, item in value.items()}
                return value

            structured = visit(structured)
            encoded = canonical(structured).decode()
            masked, changes = _mask_text(encoded)
            return (masked if count or changes else text), count + changes
    return _mask_text(text)


def _mask_text(text: str) -> tuple[str, int]:
    text, count = semantic._redact_repository_text(text)

    def secret(match: re.Match) -> str:
        nonlocal count
        value = match.group("value")
        if value.casefold().startswith(
            ("${{", "${", "$", "your_", "changeme", "placeholder", "[redacted]")
        ):
            return match.group(0)
        count += 1
        return match.group(1) + match.group("quote") + "[REDACTED]" + match.group("quote")

    text = JSON_SECRET.sub(secret, text)
    text, auth_count = AUTH_SECRET.subn(lambda m: m.group(1) + "[REDACTED]", text)
    count += auth_count

    def email(match: re.Match) -> str:
        return "[email:" + digest(match.group().casefold().encode())[:16] + "]"

    text, emails = EMAIL.subn(email, text)
    return text, count + emails


class CorpusArchive:
    """Private raw evidence plus a separate, content-addressed sanitized view.

    Capture time belongs to the observation journal, never to content identity.
    Raw originals are not uploaded to GitHub artifacts or the Vector Store.
    """

    def __init__(self, directory: Path, repository: str):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise ValueError("repository_invalid")
        self.directory = directory
        self.repository = repository
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory.chmod(0o700)
        for name in ("objects", "documents", "cache", "packs"):
            (directory / name).mkdir(exist_ok=True, mode=0o700)
        self.cache_dir = directory / "cache"
        identity = directory / "identity.json"
        if identity.exists() and json.loads(identity.read_bytes()) != {
            "schema": SCHEMA,
            "repository": repository,
        }:
            raise ValueError("archive_identity_mismatch")
        atomic(identity, canonical({"schema": SCHEMA, "repository": repository}))
        self.entries = {}
        catalogue = directory / "catalogue.json"
        if catalogue.exists():
            self.entries = json.loads(catalogue.read_bytes())
        self.gaps = (
            json.loads((directory / "gaps.json").read_bytes())
            if (directory / "gaps.json").exists()
            else []
        )
        self.observed = set()
        self.captured_at = datetime.now(timezone.utc).isoformat()
        self.expanded_bytes = 0

    def gap(self, kind: str, source_id: str, reason: str, metadata: dict | None = None) -> None:
        item = {"kind": kind, "source_id": source_id, "reason": reason, "metadata": metadata or {}}
        # Error strings and URLs from upstream are data, never printed verbatim.
        item = json.loads(safe_text(canonical(item).decode())[0])
        self.gaps.append(item)

    def add(
        self,
        kind: str,
        source_id: str,
        url: str,
        version: str,
        raw: bytes,
        metadata: dict | None = None,
        *,
        _depth: int = 0,
    ) -> None:
        if not isinstance(raw, bytes):
            raise TypeError("source_bytes_required")
        if len(raw) > MAX_SOURCE_BYTES:
            self.gap(kind, source_id, "source_byte_limit", {"bytes": len(raw)})
            return
        raw_hash = digest(raw)
        self._store_original(raw_hash, raw)
        identity = {
            "redaction_policy": REDACTION_POLICY,
            "repository": self.repository,
            "kind": kind,
            "source_id": source_id,
            "url": url,
            "version": version,
            "raw_sha256": raw_hash,
            "raw_bytes": len(raw),
            "metadata": metadata or {},
        }
        identity = json.loads(safe_text(canonical(identity).decode())[0])
        key = digest(canonical(identity))
        self.observed.add(key)
        entry = {**identity, "archive": "saved_private", "index": "not_uploaded"}
        try:
            text = raw.decode("utf-8")
            if "\0" in text:
                raise UnicodeDecodeError("utf-8", raw, 0, 1, "binary")
        except UnicodeDecodeError:
            text = None
        if text is not None:
            text, redactions = safe_text(text)
            entry["redactions"] = redactions
            entry["text_sha256"] = digest(text.encode())
            document = (
                "# Repository evidence\n\n"
                + canonical({k: v for k, v in identity.items() if k != "metadata"}).decode()
                + "\n\nSource text (untrusted evidence):\n\n"
                + text
                + "\n"
            ).encode()
            document_hash = digest(document)
            atomic(self.directory / "documents" / (document_hash + ".md"), document)
            entry["document_sha256"] = document_hash
            entry["index"] = "prepared"
            if metadata:
                provenance = (
                    "# Repository source provenance\n\n"
                    + canonical({**identity, "document_sha256": document_hash}).decode()
                    + "\n"
                ).encode()
                provenance_hash = digest(provenance)
                atomic(self.directory / "documents" / (provenance_hash + ".md"), provenance)
                entry["provenance_document_sha256"] = provenance_hash
        else:
            entry["index"] = "not_text"
            if _depth < 2 and zipfile.is_zipfile(io.BytesIO(raw)):
                self._zip(raw, kind, source_id, url, version, _depth)
            else:
                from scripts.repository_corpus_binary import extract_binary

                if not extract_binary(self, raw, kind, source_id, url, version, _depth):
                    self.gap(kind, source_id, "binary_text_extraction_unavailable")
        self.entries[key] = entry
        with (self.directory / "observations.jsonl").open("ab") as stream:
            stream.write(canonical({"source": key, "captured_at": self.captured_at}) + b"\n")
        if len(self.observed) % 1000 == 0:
            self.checkpoint()

    def _store_original(self, raw_hash: str, raw: bytes) -> None:
        original = self.directory / "objects" / raw_hash
        if not original.exists():
            atomic(original, raw)
        elif digest(original.read_bytes()) != raw_hash:
            raise ValueError("archive_object_corrupt")

    def _zip(self, raw: bytes, kind: str, source_id: str, url: str, version: str, depth: int):
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                for index, member in enumerate(archive.infolist()):
                    member_id = source_id + "!/" + member.filename
                    if member.is_dir():
                        continue
                    if index >= 10000 or self.expanded_bytes + member.file_size > 1024**3:
                        self.gap(kind, source_id, "archive_expansion_limit")
                        break
                    if (
                        member.filename.startswith("/")
                        or ".." in member.filename.split("/")
                        or semantic._repository_path_is_sensitive(member.filename)
                    ):
                        self.gap(kind, member_id, "sensitive_or_unsafe_archive_member")
                        continue
                    if member.flag_bits & 1 or member.file_size > MAX_SOURCE_BYTES:
                        self.gap(kind, member_id, "encrypted_or_large_archive_member")
                        continue
                    self.expanded_bytes += member.file_size
                    self.add(
                        kind + "_member",
                        member_id,
                        url,
                        version,
                        archive.read(member),
                        {"container_sha256": digest(raw), "member": member.filename},
                        _depth=depth + 1,
                    )
        except (OSError, ValueError, RuntimeError, zipfile.BadZipFile):
            self.gap(kind, source_id, "archive_read_failed")

    def checkpoint(self) -> None:
        atomic(self.directory / "catalogue.json", canonical(self.entries))
        atomic(self.directory / "gaps.json", canonical(self.gaps))

    def summary(self) -> dict:
        originals = sum(item.get("archive") == "saved_private" for item in self.entries.values())
        return {
            "repository": self.repository,
            "captured_at": self.captured_at,
            "sources_observed": len(self.observed),
            "source_versions_archived": len(self.entries),
            "raw_originals_available": originals,
            "raw_originals_missing": len(self.entries) - originals,
            "documents_prepared": len(list((self.directory / "documents").glob("*.md"))),
            "source_counts": dict(Counter(item["kind"] for item in self.entries.values())),
            "gap_count": len(self.gaps),
            "gap_reasons": dict(Counter(item["reason"] for item in self.gaps)),
            "coverage": "partial" if self.gaps else "collected_declared_scope",
            "raw_archive": "local_private_not_a_durable_remote_backup"
            if originals == len(self.entries)
            else "sanitized_provider_copies_without_all_raw_originals",
            "generation_requests": 0,
        }


def make_pack(repository: str, documents: list[bytes]) -> bytes:
    manifest = {"repository": repository, "documents": []}
    offset = 0
    for document in documents:
        manifest["documents"].append(
            {"sha256": digest(document), "offset": offset, "bytes": len(document)}
        )
        offset += len(document)
    header = PACK_HEADER + canonical(manifest).decode() + " -->\n"
    return header.encode() + b"".join(documents)


def unpack(repository: str, content: bytes) -> dict[str, bytes]:
    first, body = content.split(b"\n", 1)
    if not first.startswith(PACK_HEADER.encode()) or not first.endswith(b" -->"):
        raise ValueError("pack_header_invalid")
    header = json.loads(first[len(PACK_HEADER) : -4])
    if not isinstance(header, dict) or not isinstance(header.get("documents"), list):
        raise ValueError("pack_header_invalid")
    if header.get("repository") != repository:
        raise ValueError("pack_repository_mismatch")
    output = {}
    offset = 0
    for item in header["documents"]:
        if (
            not isinstance(item, dict)
            or item.get("offset") != offset
            or type(item.get("bytes")) is not int
            or item["bytes"] < 1
        ):
            raise ValueError("pack_layout_invalid")
        document = body[offset : offset + item["bytes"]]
        if digest(document) != item["sha256"] or item["sha256"] in output:
            raise ValueError("pack_document_hash_mismatch")
        output[item["sha256"]] = document
        offset += item["bytes"]
    if offset != len(body):
        raise ValueError("pack_layout_invalid")
    return output


def prepared_documents(archive: CorpusArchive) -> dict[str, Path]:
    required = set()
    for entry in archive.entries.values():
        if entry.get("redaction_policy") != REDACTION_POLICY:
            raise ValueError("prepared_redaction_policy_mismatch")
        for field in ("document_sha256", "provenance_document_sha256"):
            if entry.get(field):
                required.add(entry[field])
    paths = {path.stem: path for path in (archive.directory / "documents").glob("*.md")}
    if set(paths) != required:
        raise ValueError("prepared_document_manifest_mismatch")
    for sha, path in paths.items():
        if not re.fullmatch(r"[a-f0-9]{64}", sha) or digest(path.read_bytes()) != sha:
            raise ValueError("prepared_document_hash_mismatch")
    return paths


def _validate_store(store_id: str, project: str, key: str) -> str:
    if not project or not key:
        raise ValueError("knowledge_provider_credentials_missing")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", store_id):
        raise ValueError("knowledge_store_id_invalid")
    store_path = "/vector_stores/" + quote(store_id, safe="")
    store = semantic.yandex_json_request("GET", store_path, project, key)
    if (
        store.get("id") != store_id
        or not str(store.get("name", "")).startswith(("chat-repo-", "chat-task-history-"))
        or store.get("status") in {"failed", "expired", "cancelled"}
    ):
        raise ValueError("knowledge_existing_project_store_invalid")
    return store_path


def _read_pack(archive, item, prefix, project, key):
    if not isinstance(item, dict):
        raise ValueError("vector_store_file_record_invalid")
    filename = item.get("filename", "")
    if not isinstance(filename, str):
        raise ValueError("vector_store_filename_invalid")
    if not filename.startswith(prefix):
        return None
    file_id = item.get("id")
    if not isinstance(file_id, str) or not file_id:
        raise ValueError("vector_store_file_id_invalid")
    raw = semantic.yandex_api_request(
        "GET",
        "/files/" + quote(file_id, safe="") + "/content",
        project,
        key,
        max_response_bytes=semantic.MAX_VECTOR_FILE_BYTES,
    )
    if filename != prefix + digest(raw) + ".md":
        raise ValueError("existing_pack_hash_mismatch")
    documents = unpack(archive.repository, raw)
    return file_id, raw, documents


def _existing_packs(archive, store_path, prefix, required, project, key):
    available = semantic.list_yandex_objects("/files", project, key, ("files",))
    attached = semantic.list_yandex_objects(store_path + "/files", project, key, ("files",))
    attached_ids = {semantic._attached_file_id(item) for item in attached}
    known = set()
    pack_records = []
    for item in available:
        record = _read_pack(archive, item, prefix, project, key)
        if record is None:
            continue
        file_id, raw, documents = record
        selected = set(documents) & required
        attachment_allowed = set(documents) <= required
        if not selected or (not attachment_allowed and file_id not in attached_ids):
            # An already attached historical pack can represent unchanged current
            # documents. Never newly attach its excluded historical contents.
            continue
        known.update(selected)
        pack_records.append(
            {
                "file_id": file_id,
                "sha256": digest(raw),
                "documents": sorted(selected),
                "pack_document_count": len(documents),
                "excluded_from_current_scope_count": len(documents) - len(selected),
                "attachment_allowed": attachment_allowed,
                "reused": True,
            }
        )
    return attached_ids, known, pack_records


def _send_pack(archive, pending, prefix, project, key):
    raw = make_pack(archive.repository, pending)
    if len(raw) > semantic.MAX_VECTOR_FILE_BYTES:
        raise ValueError("pack_file_limit")
    filename = prefix + digest(raw) + ".md"
    # Reuse the existing Files upload transport. All known pack names were
    # content-verified above; unchanged sources never enter a new upload.
    body, content_type = semantic._multipart_file(filename, raw)
    result = json.loads(
        semantic.yandex_api_request(
            "POST", "/files", project, key, body=body, content_type=content_type
        )
    )
    if not isinstance(result, dict):
        raise ValueError("vector_store_upload_receipt_invalid")
    file_id = result.get("id")
    if (
        not isinstance(file_id, str)
        or not file_id
        or result.get("filename") != filename
        or type(result.get("bytes")) is not int
        or result.get("bytes") != len(raw)
    ):
        raise ValueError("vector_store_upload_receipt_invalid")
    return {
        "file_id": file_id,
        "sha256": digest(raw),
        "documents": [digest(item) for item in pending],
        "reused": False,
    }


def _upload_new_packs(archive, documents_to_upload, known, prefix, pack_records, project, key):
    pending = []
    pending_bytes = 0
    for sha, path in sorted(documents_to_upload.items()):
        raw = path.read_bytes()
        if sha in known:
            continue
        if pending and pending_bytes + len(raw) > PACK_BYTES:
            pack_records.append(_send_pack(archive, pending, prefix, project, key))
            atomic(archive.directory / "upload-checkpoint.json", canonical(pack_records))
            pending, pending_bytes = [], 0
        pending.append(raw)
        pending_bytes += len(raw)
    if pending:
        pack_records.append(_send_pack(archive, pending, prefix, project, key))
        atomic(archive.directory / "upload-checkpoint.json", canonical(pack_records))


def _index_rows(store_path, project, key):
    rows = semantic.list_yandex_objects(store_path + "/files", project, key, ("files",))
    ids = [semantic._attached_file_id(item) for item in rows]
    if None in ids or len(set(ids)) != len(ids):
        raise ValueError("vector_store_file_id_invalid")
    return {semantic._attached_file_id(item): item for item in rows}


def _attach_pending(store_path, pending, capacity, project, key):
    submitted = set()
    for item in pending[:capacity]:
        if not item.get("attachment_allowed", True):
            raise ValueError("mixed_historical_pack_attachment_forbidden")
        try:
            result = semantic.yandex_json_request(
                "POST",
                store_path + "/files",
                project,
                key,
                {
                    "file_id": item["file_id"],
                    "chunking_strategy": semantic.vector_store_chunking_strategy(),
                },
            )
        except semantic.YandexHTTPError as exc:
            if exc.status != 429:
                raise
            return submitted, exc
        if (result.get("file_id") or result.get("id")) != item["file_id"]:
            raise ValueError("vector_store_attachment_receipt_invalid")
        submitted.add(item["file_id"])
    return submitted, None


def _index_progress(target_ids, by_id, submitted, checkpoint_path):
    states = {item: by_id.get(item, {}).get("status", "not_visible") for item in sorted(target_ids)}
    progress = {
        "files": len(target_ids),
        "submitted": len(target_ids & submitted),
        "completed": sum(state == "completed" for state in states.values()),
        "in_progress": sum(state == "in_progress" for state in states.values()),
    }
    if checkpoint_path:
        atomic(
            checkpoint_path,
            canonical({"progress": progress, "files": states, "generation_requests": 0}),
        )
    return states, progress


def _attach_and_wait(store_path, selected, attached_ids, project, key, *, checkpoint_path=None):
    if any(
        not item.get("attachment_allowed", True) and item["file_id"] not in attached_ids
        for item in selected
    ):
        raise ValueError("mixed_historical_pack_attachment_forbidden")
    target_ids = {item["file_id"] for item in selected}
    submitted = set(attached_ids)
    deadline = time.monotonic() + INDEX_TIMEOUT_SECONDS
    rate_failures = 0
    previous_progress = None
    next_report = 0.0
    while True:
        by_id = _index_rows(store_path, project, key)
        submitted.update(by_id)
        states, progress = _index_progress(target_ids, by_id, submitted, checkpoint_path)
        if progress != previous_progress or time.monotonic() >= next_report:
            print(json.dumps({"indexing_progress": progress}), flush=True)
            previous_progress, next_report = progress, time.monotonic() + 60
        if any(state in {"failed", "cancelled", "expired"} for state in states.values()):
            raise ValueError("vector_store_index_failed")
        if all(state == "completed" for state in states.values()):
            return
        if time.monotonic() >= deadline:
            raise ValueError("vector_store_index_timeout")
        active = sum(
            item.get("status") not in {"completed", "failed", "cancelled", "expired"}
            for item in by_id.values()
        )
        active += len(submitted - set(by_id))
        pending = [item for item in selected if item["file_id"] not in submitted]
        added, error = _attach_pending(
            store_path, pending, max(0, INDEX_MAX_IN_FLIGHT - active), project, key
        )
        submitted.update(added)
        rate_failures = rate_failures + 1 if error else 0
        if error and rate_failures >= 3:
            raise error
        if added and error is None:
            continue
        time.sleep(30 if error else 10)


def upload_archive(archive: CorpusArchive, store_id: str, project: str, key: str) -> dict:
    """Reuse verified packs; append only previously unrepresented documents."""
    if not project or not key:
        raise ValueError("knowledge_provider_credentials_missing")
    documents_to_upload = prepared_documents(archive)
    required = set(documents_to_upload)
    if not required:
        raise ValueError("empty_repository_corpus")
    store_path = _validate_store(store_id, project, key)
    prefix = "chat-repository-archive-v1-" + digest(archive.repository.encode())[:16] + "-"
    attached_ids, known, pack_records = _existing_packs(
        archive, store_path, prefix, required, project, key
    )
    _upload_new_packs(archive, documents_to_upload, known, prefix, pack_records, project, key)
    selected = [item for item in pack_records if required.intersection(item["documents"])]
    _attach_and_wait(
        store_path,
        selected,
        attached_ids,
        project,
        key,
        checkpoint_path=archive.directory / "index-checkpoint.json",
    )
    return {
        "status": "indexed_available_corpus",
        "store_id": store_id,
        "documents": len(required),
        "packs": selected,
        "generation_requests": 0,
    }


def provider_inventory(repository: str, store_id: str, project: str, key: str) -> dict:
    """Inspect upload/attachment state without collection, generation or writes."""
    store_path = _validate_store(store_id, project, key)
    prefix = "chat-repository-archive-v1-" + digest(repository.encode())[:16] + "-"
    files = semantic.list_yandex_objects("/files", project, key, ("files",))
    attached = semantic.list_yandex_objects(store_path + "/files", project, key, ("files",))
    candidates = [
        {field: item.get(field) for field in ("id", "filename", "bytes")}
        for item in files
        if str(item.get("filename", "")).startswith(prefix)
    ]
    ids = {item["id"] for item in candidates}
    return {
        "status": "observed_without_content_verification",
        "store_id": store_id,
        "project_file_count": len(files),
        "project_file_bytes": sum(
            item.get("bytes", 0) for item in files if type(item.get("bytes")) is int
        ),
        "store_attachment_count": len(attached),
        "candidate_pack_files": candidates,
        "attachments": [
            {"file_id": semantic._attached_file_id(item), "status": item.get("status")}
            for item in attached
            if semantic._attached_file_id(item) in ids
        ],
        "generation_requests": 0,
    }


def _snippet_candidates(archive, document_hashes, file_id, snippet):
    candidates = []
    for sha in sorted(document_hashes):
        path = archive.directory / "documents" / (sha + ".md")
        if not path.exists():
            continue
        raw = path.read_bytes()
        if digest(raw) != sha:
            raise ValueError("prepared_document_hash_mismatch")
        text = raw.decode()
        # Match source contents only: a shared header or URL is not
        # evidence that the source text was retrieved.
        separator = "\n\nSource text (untrusted evidence):\n\n"
        if separator not in text:
            continue
        source_text = text.split(separator, 1)[1].removesuffix("\n")
        excerpt = semantic._longest_exact_excerpt(snippet, source_text)
        if excerpt:
            start, end = excerpt
            source = json.loads(text.split("\n\n", 2)[1])
            candidates.append(
                {
                    "file_id": file_id,
                    "document_sha256": sha,
                    "source": source,
                    "start": start,
                    "end": end,
                    "quote": source_text[start:end],
                }
            )
    return candidates


def verify_search(archive: CorpusArchive, upload: dict, query: str, project: str, key: str) -> dict:
    """Search without generation; bind returned text to indexed file and document."""
    packs = {item["file_id"]: set(item["documents"]) for item in upload["packs"]}
    matches = []
    for item in semantic.vector_store_search(upload["store_id"], query, project, key):
        file_id = item.get("file_id")
        if file_id not in packs:
            continue
        for snippet in semantic._vector_result_texts(item):
            candidates = _snippet_candidates(archive, packs[file_id], file_id, snippet)
            if candidates:
                maximum = max(len(candidate["quote"]) for candidate in candidates)
                candidates = [
                    candidate for candidate in candidates if len(candidate["quote"]) == maximum
                ]
                if len(candidates) == 1:
                    matches.append({**candidates[0], "attribution": "unique"})
                else:
                    matches.append(
                        {"file_id": file_id, "attribution": "ambiguous", "candidates": candidates}
                    )
    marker = "REPOSITORY_RAG_ABSENT_" + os.urandom(16).hex()
    absent = semantic.vector_store_search(upload["store_id"], marker, project, key)
    fabricated = any(
        marker in text for item in absent for text in semantic._vector_result_texts(item)
    )
    return {
        "status": "verified_fragments"
        if any(item["attribution"] == "unique" for item in matches) and not fabricated
        else "failed",
        "query": query,
        "matches": matches[:20],
        "negative_control": {"marker_present": fabricated, "result_count": len(absent)},
        "generation_requests": 0,
        "semantic_entailment": "not_tested",
    }


def _arguments(argv: list[str] | None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default="maksimp6/Chat")
    parser.add_argument("--git-root", type=Path, default=Path.cwd())
    parser.add_argument("--archive-dir", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--skip-github", action="store_true")
    parser.add_argument("--upload", action="store_true")
    parser.add_argument("--upload-only", action="store_true")
    parser.add_argument("--provider-check", action="store_true")
    parser.add_argument("--resume-receipt", type=Path)
    parser.add_argument("--store-id", default=DEFAULT_STORE_ID)
    parser.add_argument(
        "--query", default="Как устроено исполнение инструментов UniversalToolExecutor?"
    )
    parser.add_argument("--github-max-requests", type=int, default=1500)
    parser.add_argument("--fetch-status", type=Path)
    return parser.parse_args(argv)


def _collect(args, archive: CorpusArchive, result: dict) -> None:
    if not args.upload_only:
        archive.gaps = []
        if args.fetch_status and args.fetch_status.exists():
            for row in json.loads(args.fetch_status.read_bytes()):
                archive.gap("git_fetch", row["source"], row["reason"])
        from scripts.repository_corpus_git import collect_git

        collect_git(args.git_root, archive)
        if not args.skip_github:
            from scripts.repository_corpus_github import collect_github

            result["github_collection"] = collect_github(
                args.repository,
                os.environ.get("GITHUB_TOKEN") or os.environ.get("CODEX_GITHUB_TOKEN", ""),
                archive,
                max_requests=args.github_max_requests,
            )
        else:
            archive.gap("github", args.repository, "github_collection_not_run")


def _run(args, archive: CorpusArchive, result: dict) -> None:
    project, key = os.environ.get("YANDEX_PROJECT_ID", ""), os.environ.get("YANDEX_API_KEY", "")
    if args.provider_check:
        result["provider_inventory"] = provider_inventory(
            args.repository, args.store_id, project, key
        )
        result["status"] = "provider_inspected"
        return
    if args.resume_receipt:
        from scripts.repository_rag_resume import resume_from_receipt

        result["upload"] = resume_from_receipt(
            archive, args.resume_receipt, args.store_id, project, key
        )
    else:
        _collect(args, archive, result)
    archive.checkpoint()
    result.update({**archive.summary(), "status": "prepared"})
    if not args.resume_receipt and (args.upload or args.upload_only):
        result["upload"] = upload_archive(archive, args.store_id, project, key)
    if "upload" in result:
        result["retrieval"] = verify_search(archive, result["upload"], args.query, project, key)
        result["status"] = "indexed_partial" if archive.gaps else "indexed_declared_scope"
        if result["retrieval"]["status"] != "verified_fragments":
            result["status"] = "retrieval_verification_failed"


def main(argv: list[str] | None = None) -> int:
    args = _arguments(argv)
    args.archive_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = (args.archive_dir / ".writer.lock").open("a")
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        print('{"status":"blocked","error_code":"archive_writer_active","generation_requests":0}')
        return 1
    archive = CorpusArchive(args.archive_dir, args.repository)
    result = {"status": "collecting", "generation_requests": 0}
    try:
        _run(args, archive, result)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        result = {
            **archive.summary(),
            **result,
            "status": "blocked",
            "error_type": type(exc).__name__,
        }
        if re.fullmatch(r"[a-z][a-z0-9_]{1,100}", str(exc)):
            result["error_code"] = str(exc)
        if isinstance(exc, semantic.YandexHTTPError):
            result["http_status"] = exc.status
            result["provider_method"] = exc.method
            result["provider_path"] = exc.path
            result["provider_code"] = exc.provider_code
    finally:
        archive.checkpoint()
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        atomic(args.receipt, canonical(result))
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()
    print(
        json.dumps(
            {key: value for key, value in result.items() if key not in {"upload", "retrieval"}},
            ensure_ascii=False,
        )
    )
    return 1 if result["status"] in {"blocked", "retrieval_verification_failed"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
