"""Retrieval source adapters for memory, deterministic code indexes and summaries."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable, Protocol

from agent_memory import AgentMemoryStore

from .models import RetrievalDocument, RetrievalQuery


@dataclass(frozen=True)
class SourceBatch:
    documents: tuple[RetrievalDocument, ...]
    stale_filtered: int = 0
    visibility_filtered: int = 0
    sensitive_filtered: int = 0


class RetrievalSource(Protocol):
    source_type: str

    def collect(self, query: RetrievalQuery) -> SourceBatch: ...


def _compact_json(value: Any, *, max_chars: int = 2000) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."


class MemorySource:
    source_type = "memory"

    def __init__(self, store: AgentMemoryStore) -> None:
        self._store = store

    def collect(self, query: RetrievalQuery) -> SourceBatch:
        records = self._store.list_records(scope=query.memory_scope, status="active")
        documents: list[RetrievalDocument] = []
        stale_filtered = 0
        visibility_filtered = 0
        sensitive_filtered = 0

        for record in records:
            if query.memory_kinds and record.kind not in query.memory_kinds:
                continue
            if record.visibility and (query.role is None or query.role not in record.visibility):
                visibility_filtered += 1
                continue
            if record.sensitivity == "sensitive" and not query.include_sensitive:
                sensitive_filtered += 1
                continue

            data = record.as_dict()
            freshness = data["freshness"]
            head_bound = bool(freshness.get("head_bound"))
            if head_bound and record.source_version != query.head_sha:
                stale_filtered += 1
                continue

            provenance = data["provenance"]
            repository = str(provenance.get("repository") or "")
            if repository and repository != query.repository:
                continue
            payload_text = _compact_json(data["payload"])
            text = record.text if payload_text == "{}" else f"{record.text}\n{payload_text}"
            documents.append(
                RetrievalDocument(
                    document_id=f"memory:{record.memory_id}",
                    source_type="memory",
                    text=text,
                    refs=tuple(str(item) for item in provenance["refs"]),
                    source_version=record.source_version,
                    metadata={
                        "kind": record.kind,
                        "scope": record.scope,
                        "confidence": record.confidence,
                        "provenance": provenance,
                    },
                    sensitivity=record.sensitivity,
                    visibility=record.visibility,
                    head_bound=head_bound,
                )
            )

        return SourceBatch(
            documents=tuple(documents),
            stale_filtered=stale_filtered,
            visibility_filtered=visibility_filtered,
            sensitive_filtered=sensitive_filtered,
        )


class AiIndexSource:
    source_type = "code_index"

    def __init__(self, index: dict[str, Any], *, repository: str, head_sha: str) -> None:
        self._index = index
        self._repository = str(repository)
        self._head_sha = str(head_sha)

    def collect(self, query: RetrievalQuery) -> SourceBatch:
        if query.repository != self._repository or query.head_sha != self._head_sha:
            return SourceBatch(documents=(), stale_filtered=len(self._index.get("files", [])))

        tests_by_module = self._index.get("tests_by_module") or {}
        documents: list[RetrievalDocument] = []
        for item in self._index.get("files", []):
            path = str(item.get("path") or "")
            module = str(item.get("module") or "")
            if not path:
                continue
            symbols = [
                str(symbol.get("qualified_name") or symbol.get("name") or "")
                for symbol in item.get("symbols", [])
                if symbol.get("qualified_name") or symbol.get("name")
            ]
            calls = [str(value) for value in item.get("calls", [])]
            imports = [
                ".".join(
                    part
                    for part in (
                        str(value.get("module") or ""),
                        str(value.get("name") or ""),
                    )
                    if part
                )
                for value in item.get("imports", [])
            ]
            linked_tests = [str(value) for value in tests_by_module.get(module, [])]
            text = " ".join(
                value
                for value in (
                    path,
                    module,
                    " ".join(symbols),
                    " ".join(calls),
                    " ".join(imports),
                    " ".join(linked_tests),
                )
                if value
            )
            documents.append(
                RetrievalDocument(
                    document_id=f"code:{path}",
                    source_type="code_index",
                    text=text,
                    refs=(path,),
                    source_version=self._head_sha,
                    metadata={
                        "path": path,
                        "module": module,
                        "sha256": str(item.get("sha256") or ""),
                        "is_test": bool(item.get("is_test")),
                        "symbols": symbols,
                        "tests": linked_tests,
                    },
                    sensitivity="internal",
                    head_bound=True,
                )
            )
        return SourceBatch(documents=tuple(documents))


class StaticSource:
    """Adapter for already-normalized GitHub/CI/trace/doc summaries."""

    def __init__(self, source_type: str, documents: Iterable[RetrievalDocument]) -> None:
        self.source_type = str(source_type)
        self._documents = tuple(documents)

    def collect(self, query: RetrievalQuery) -> SourceBatch:
        documents: list[RetrievalDocument] = []
        stale_filtered = 0
        visibility_filtered = 0
        sensitive_filtered = 0

        for document in self._documents:
            if document.source_type != self.source_type:
                continue
            repository = str(document.metadata.get("repository") or "")
            if repository and repository != query.repository:
                continue
            if document.head_bound and document.source_version != query.head_sha:
                stale_filtered += 1
                continue
            if document.visibility and (
                query.role is None or query.role not in document.visibility
            ):
                visibility_filtered += 1
                continue
            if document.sensitivity == "sensitive" and not query.include_sensitive:
                sensitive_filtered += 1
                continue
            documents.append(document)

        return SourceBatch(
            documents=tuple(documents),
            stale_filtered=stale_filtered,
            visibility_filtered=visibility_filtered,
            sensitive_filtered=sensitive_filtered,
        )
