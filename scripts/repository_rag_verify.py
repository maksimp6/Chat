"""Read-only retrieval proof for completed hit packs, never full-corpus acceptance."""

from __future__ import annotations

import os
from pathlib import Path
import re
import tempfile
from types import ModuleType, SimpleNamespace
from typing import Any, TYPE_CHECKING
from urllib.parse import quote

from scripts import repository_rag_resume as resume

if TYPE_CHECKING:
    from scripts.repository_rag import CorpusArchive

Record = dict[str, Any]
MAX_HIT_PACKS = 10
MAX_DOWNLOAD_BYTES = 64 * 1024 * 1024


def direct_search(
    store_id: str, query: str, project: str, key: str, rag: ModuleType
) -> list[Record]:
    """Read through vector search with provider query rewriting explicitly disabled."""
    data = rag.semantic.yandex_json_request(
        "POST",
        "/vector_stores/" + quote(store_id, safe="") + "/search",
        project,
        key,
        {
            "query": query,
            "rewrite_query": False,
            "max_num_results": rag.semantic.MAX_VECTOR_SEARCH_RESULTS,
            "ranking_options": {"ranker": "auto", "score_threshold": 0.0},
        },
    )
    if not isinstance(data, dict):
        raise ValueError("vector_store_search_response_invalid")
    results = data.get("data", data.get("results", data.get("hits")))
    if not isinstance(results, list) or any(not isinstance(item, dict) for item in results):
        raise ValueError("vector_store_search_response_invalid")
    return results[: rag.semantic.MAX_VECTOR_SEARCH_RESULTS]


def _selection(
    archive: CorpusArchive, inventory: Record, rag: ModuleType
) -> tuple[str, dict[str, Record]]:
    if (
        not isinstance(inventory, dict)
        or inventory.get("status") != "observed_without_content_verification"
        or type(inventory.get("generation_requests")) is not int
        or inventory["generation_requests"] != 0
        or not isinstance(inventory.get("store_id"), str)
        or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", inventory["store_id"])
        or not isinstance(inventory.get("candidate_pack_files"), list)
        or not isinstance(inventory.get("attachments"), list)
    ):
        raise ValueError("verify_inventory_invalid")
    prefix = "chat-repository-archive-v1-" + rag.digest(archive.repository.encode())[:16] + "-"
    files, names = {}, set()
    for item in inventory["candidate_pack_files"]:
        resume._file_record(item, prefix, rag)
        if item["id"] in files or item["filename"] in names:
            raise ValueError("verify_candidate_ambiguous")
        files[item["id"]] = item
        names.add(item["filename"])
    states = {}
    for item in inventory["attachments"]:
        if not isinstance(item, dict) or not isinstance(item.get("file_id"), str):
            raise ValueError("verify_attachment_invalid")
        if item["file_id"] in states or not isinstance(item.get("status"), str):
            raise ValueError("verify_attachment_invalid")
        states[item["file_id"]] = item["status"]
    return prefix, {key: value for key, value in files.items() if states.get(key) == "completed"}


def _download_hits(
    archive: CorpusArchive,
    hits: list[Record],
    eligible: dict[str, Record],
    prefix: str,
    project: str,
    key: str,
    rag: ModuleType,
    stage: SimpleNamespace,
) -> tuple[dict[str, list[str]], dict[str, Record], list[Record], int]:
    packs, documents, omissions = {}, {}, []
    attempted = set()
    downloaded = 0
    for hit in hits:
        file_id = hit.get("file_id") if isinstance(hit, dict) else None
        if not isinstance(file_id, str) or file_id in attempted:
            continue
        attempted.add(file_id)
        if file_id not in eligible:
            omissions.append({"file_id": file_id, "reason": "not_a_completed_candidate_pack"})
            continue
        item = eligible[file_id]
        if len(packs) >= MAX_HIT_PACKS or item["bytes"] > MAX_DOWNLOAD_BYTES - downloaded:
            omissions.append({"file_id": file_id, "reason": "verification_download_limit"})
            continue
        record = rag._read_pack(
            archive, item, prefix, project, key, max_response_bytes=MAX_DOWNLOAD_BYTES - downloaded
        )
        if record is None:
            raise ValueError("verify_pack_not_selected")
        actual_id, raw, contents = record
        if actual_id != file_id or len(raw) != item["bytes"] or not contents:
            raise ValueError("verify_pack_size_or_identity_mismatch")
        downloaded += len(raw)
        if downloaded > MAX_DOWNLOAD_BYTES:
            raise ValueError("verify_download_limit_exceeded")
        for sha, content in contents.items():
            parsed = resume._document(content, sha, archive.repository, rag)
            if sha in documents and documents[sha] != parsed:
                raise ValueError("verify_document_identity_conflict")
            documents[sha] = parsed
            rag.atomic(stage.directory / "documents" / (sha + ".md"), content)
        packs[file_id] = sorted(contents)
    return packs, documents, omissions, downloaded


def _provenance(documents: dict[str, Record]) -> dict[str, list[Record]]:
    result = {}
    for sha, item in documents.items():
        if item["kind"] != "provenance":
            continue
        target = documents.get(item["target"])
        if target is None:
            continue
        if target["kind"] != "evidence" or target["identity"] != item["identity"]:
            raise ValueError("verify_provenance_content_mismatch")
        result.setdefault(item["target"], []).append(
            {"document_sha256": sha, "metadata": item["metadata"]}
        )
    return result


def _matches(
    stage: SimpleNamespace,
    hits: list[Record],
    packs: dict[str, list[str]],
    documents: dict[str, Record],
    rag: ModuleType,
) -> list[Record]:
    provenance = _provenance(documents)
    matches = []
    for hit in hits:
        if not isinstance(hit, dict) or hit.get("file_id") not in packs:
            continue
        file_id = hit["file_id"]
        for snippet in rag.semantic._vector_result_texts(hit):
            candidates = rag._snippet_candidates(stage, packs[file_id], file_id, snippet)
            if not candidates:
                continue
            maximum = max(len(item["quote"]) for item in candidates)
            candidates = [item for item in candidates if len(item["quote"]) == maximum]
            for candidate in candidates:
                evidence = provenance.get(candidate["document_sha256"], [])
                candidate.update(
                    provenance_complete=bool(evidence),
                    provenance_scope="downloaded_hit_packs_only",
                    provenance_documents=evidence,
                )
            if len(candidates) == 1:
                matches.append({**candidates[0], "attribution": "unique"})
            else:
                matches.append(
                    {"file_id": file_id, "attribution": "ambiguous", "candidates": candidates}
                )
    return matches


def verify_available(
    archive: CorpusArchive,
    inventory: Record,
    query: str,
    project: str,
    key: str,
    rag: ModuleType,
) -> Record:
    """Verify direct search evidence without waiting for or modifying failed packs."""
    prefix, eligible = _selection(archive, inventory, rag)
    hits = direct_search(inventory["store_id"], query, project, key, rag)
    if not isinstance(hits, list) or not all(isinstance(item, dict) for item in hits):
        raise ValueError("verify_search_response_invalid")
    with tempfile.TemporaryDirectory(
        prefix="verified-hit-packs-", dir=archive.directory
    ) as directory:
        stage = SimpleNamespace(directory=Path(directory), repository=archive.repository)
        (stage.directory / "documents").mkdir(mode=0o700)
        packs, documents, omissions, downloaded = _download_hits(
            archive, hits, eligible, prefix, project, key, rag, stage
        )
        matches = _matches(stage, hits, packs, documents, rag)
    marker = "REPOSITORY_RAG_ABSENT_" + os.urandom(16).hex()
    absent = direct_search(inventory["store_id"], marker, project, key, rag)
    if not isinstance(absent, list) or not all(isinstance(item, dict) for item in absent):
        raise ValueError("verify_search_response_invalid")
    fabricated = any(
        marker in text for item in absent for text in rag.semantic._vector_result_texts(item)
    )
    return {
        "status": "verified_fragments"
        if any(item["attribution"] == "unique" for item in matches) and not fabricated
        else "failed",
        "scope": "completed_retrieved_packs_only",
        "coverage": "partial",
        "corpus_complete": False,
        "store_id": inventory["store_id"],
        "query": query,
        "completed_candidate_packs": len(eligible),
        "verified_pack_ids": sorted(packs),
        "documents_verified": len(documents),
        "downloaded_bytes": downloaded,
        "download_limits": {"packs": MAX_HIT_PACKS, "bytes": MAX_DOWNLOAD_BYTES},
        "matches": matches[:20],
        "omissions": omissions,
        "negative_control": {"marker_present": fabricated, "result_count": len(absent)},
        "search_requests": 2,
        "query_rewriting": False,
        "provider_writes": 0,
        "generation_requests": 0,
        "raw_originals_recovered": False,
        "semantic_entailment": "not_tested",
    }
