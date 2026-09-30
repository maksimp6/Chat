"""Token-bounded hybrid retrieval for agent task packets."""

from __future__ import annotations

from collections import Counter
import json
import math
import re
from typing import Any, Mapping

from agent_context import CacheLookup
from agent_memory import AgentMemoryStore, MemoryRecord

from .models import RetrievalBundle, RetrievalHit, RetrievalQuery


_TOKEN_RE = re.compile(r"[\w./#:-]+", re.UNICODE)


def _tokens(value: str) -> list[str]:
    return [token for token in _TOKEN_RE.findall(str(value).lower()) if len(token) > 1]


def _bm25_scores(query: str, documents: list[str]) -> list[float]:
    if not documents:
        return []
    query_tokens = _tokens(query)
    if not query_tokens:
        return [0.0] * len(documents)

    tokenized = [_tokens(document) for document in documents]
    lengths = [len(tokens) for tokens in tokenized]
    avg_len = sum(lengths) / max(len(lengths), 1) or 1.0
    document_frequency = Counter()
    for tokens in tokenized:
        document_frequency.update(set(tokens))

    scores: list[float] = []
    count = len(documents)
    k1 = 1.5
    b = 0.75
    for tokens, length in zip(tokenized, lengths, strict=True):
        frequencies = Counter(tokens)
        score = 0.0
        for token in set(query_tokens):
            frequency = frequencies[token]
            if frequency <= 0:
                continue
            seen = document_frequency[token]
            inverse = math.log(1.0 + (count - seen + 0.5) / (seen + 0.5))
            denominator = frequency + k1 * (1.0 - b + b * length / avg_len)
            score += inverse * (frequency * (k1 + 1.0) / denominator)
        scores.append(score)
    return scores


def _clip(text: str, limit: int) -> str:
    value = str(text)
    if len(value) <= limit:
        return value
    return value[: max(limit - 1, 1)].rstrip() + "…"


def _memory_document(record: MemoryRecord) -> str:
    payload = record.as_dict()
    provenance = payload["provenance"]
    refs = " ".join(str(item) for item in provenance.get("refs") or [])
    compact_payload = json.dumps(
        payload["payload"],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return " ".join(
        [
            record.text,
            record.kind,
            record.scope,
            refs,
            compact_payload,
        ]
    )


def _memory_ref(record: MemoryRecord) -> str:
    refs = record.as_dict()["provenance"].get("refs") or []
    return str(refs[0]) if refs else f"memory:{record.memory_id}"


def _eligible_memory(record: MemoryRecord, query: RetrievalQuery) -> bool:
    provenance = record.as_dict()["provenance"]
    if provenance.get("source_type") == "github":
        if str(provenance.get("repository") or "") != query.repository:
            return False

    if record.kind == "task":
        return (
            str(provenance.get("work_item") or "") == query.work_item
            and str(provenance.get("head_sha") or "") == query.head_sha
        )

    if record.kind == "role" and record.scope.startswith("role:"):
        return record.scope == f"role:{query.role}"
    return True


def _memory_hits(
    store: AgentMemoryStore,
    query: RetrievalQuery,
    *,
    now: int | None,
) -> list[RetrievalHit]:
    records = [
        record
        for record in store.list_records(
            status="active",
            visible_to=query.role,
            now=now,
        )
        if _eligible_memory(record, query)
    ]
    documents = [_memory_document(record) for record in records]
    scores = _bm25_scores(query.text, documents)
    hits: list[RetrievalHit] = []
    for record, document, score in zip(records, documents, scores, strict=True):
        if score <= 0:
            continue
        boost = {"task": 4.0, "role": 2.0, "project": 1.5, "process": 1.0}.get(
            record.kind,
            0.5,
        )
        hits.append(
            RetrievalHit(
                source_type="memory",
                ref=_memory_ref(record),
                score=boost + score,
                text=record.text,
                metadata={
                    "memory_id": record.memory_id,
                    "kind": record.kind,
                    "scope": record.scope,
                    "source_version": record.source_version,
                    "provenance": record.as_dict()["provenance"],
                    "matched_chars": len(document),
                },
            )
        )
    return hits


def _code_document(item: Mapping[str, Any], tests: list[str]) -> str:
    symbols = " ".join(
        f"{symbol.get('qualified_name', '')} {symbol.get('kind', '')}"
        for symbol in item.get("symbols") or []
    )
    imports = " ".join(
        " ".join(
            str(part)
            for part in (
                imported.get("module"),
                imported.get("name"),
                imported.get("as"),
            )
            if part
        )
        for imported in item.get("imports") or []
    )
    calls = " ".join(str(call) for call in item.get("calls") or [])
    return " ".join(
        [
            str(item.get("path") or ""),
            str(item.get("module") or ""),
            symbols,
            imports,
            calls,
            " ".join(tests),
        ]
    )


def _code_hits(
    query: RetrievalQuery,
    repository_index: Mapping[str, Any] | None,
    repository_index_version: str | None,
) -> list[RetrievalHit]:
    if not repository_index or repository_index_version != query.head_sha:
        return []

    files = list(repository_index.get("files") or [])
    tests_by_module = repository_index.get("tests_by_module") or {}
    documents: list[str] = []
    related_tests: list[list[str]] = []
    for item in files:
        tests = list(tests_by_module.get(str(item.get("module") or ""), []))
        related_tests.append(tests)
        documents.append(_code_document(item, tests))

    scores = _bm25_scores(query.text, documents)
    hits: list[RetrievalHit] = []
    for item, tests, score in zip(files, related_tests, scores, strict=True):
        if score <= 0:
            continue
        symbols = [
            str(symbol.get("qualified_name") or "")
            for symbol in item.get("symbols") or []
            if symbol.get("qualified_name")
        ]
        summary = f"{item.get('path')}: symbols={', '.join(symbols[:8]) or 'none'}"
        if tests:
            summary += f"; tests={', '.join(tests[:5])}"
        hits.append(
            RetrievalHit(
                source_type="code",
                ref=str(item.get("path") or "code"),
                score=1.0 + score,
                text=summary,
                metadata={
                    "path": item.get("path"),
                    "module": item.get("module"),
                    "sha256": item.get("sha256"),
                    "symbols": symbols[:12],
                    "calls": list(item.get("calls") or [])[:20],
                    "tests": tests[:10],
                    "index_version": repository_index_version,
                },
            )
        )
    return hits


def _cache_hits(lookup: CacheLookup | None) -> list[RetrievalHit]:
    if lookup is None or lookup.status not in {"hit", "partial"}:
        return []
    hits: list[RetrievalHit] = []
    for index, context_slice in enumerate(lookup.reusable_slices):
        text = json.dumps(
            context_slice.as_dict()["payload"],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        hits.append(
            RetrievalHit(
                source_type="cache",
                ref=f"cache:{context_slice.name}",
                score=1000.0 - index,
                text=text or context_slice.name,
                metadata={
                    "slice": context_slice.name,
                    "depends_on": list(context_slice.depends_on),
                    "stale_components": list(lookup.stale_components),
                },
            )
        )
    return hits


def _bound_hits(
    candidates: list[RetrievalHit],
    query: RetrievalQuery,
) -> tuple[RetrievalHit, ...]:
    ordered = sorted(candidates, key=lambda hit: (-hit.score, hit.source_type, hit.ref))
    selected: list[RetrievalHit] = []
    remaining = query.max_chars
    for hit in ordered:
        if len(selected) >= query.max_results or remaining <= 0:
            break
        clipped = _clip(hit.text, remaining)
        if not clipped:
            continue
        selected.append(
            RetrievalHit(
                source_type=hit.source_type,
                ref=hit.ref,
                score=hit.score,
                text=clipped,
                metadata=hit.as_dict()["metadata"],
            )
        )
        remaining -= len(clipped)
    return tuple(selected)


class HybridRetriever:
    def __init__(
        self,
        *,
        memory_store: AgentMemoryStore,
        repository_index: Mapping[str, Any] | None = None,
        repository_index_version: str | None = None,
    ) -> None:
        self.memory_store = memory_store
        self.repository_index = repository_index
        self.repository_index_version = repository_index_version

    def retrieve(
        self,
        query: RetrievalQuery,
        *,
        cache_lookup: CacheLookup | None = None,
        now: int | None = None,
    ) -> RetrievalBundle:
        cache_status = cache_lookup.status if cache_lookup is not None else "miss"
        cached = _cache_hits(cache_lookup)

        if cache_status == "hit" and cached:
            hits = _bound_hits(cached, query)
            return self._bundle(
                hits,
                cache_status=cache_status,
                cache_lookup=cache_lookup,
            )

        candidates = [
            *cached,
            *_memory_hits(self.memory_store, query, now=now),
            *_code_hits(
                query,
                self.repository_index,
                self.repository_index_version,
            ),
        ]
        hits = _bound_hits(candidates, query)
        return self._bundle(
            hits,
            cache_status=cache_status,
            cache_lookup=cache_lookup,
        )

    @staticmethod
    def _bundle(
        hits: tuple[RetrievalHit, ...],
        *,
        cache_status: str,
        cache_lookup: CacheLookup | None,
    ) -> RetrievalBundle:
        counts = Counter(hit.source_type for hit in hits)
        return RetrievalBundle(
            status="hit" if hits else "miss",
            hits=hits,
            cache_status=cache_status,
            source_counts=dict(counts),
            total_chars=sum(len(hit.text) for hit in hits),
            saved_source_bytes=(
                cache_lookup.saved_source_bytes if cache_lookup is not None else 0
            ),
            saved_input_tokens=(
                cache_lookup.saved_input_tokens if cache_lookup is not None else 0
            ),
        )


def record_retrieval(trace: Any, bundle: RetrievalBundle, query: RetrievalQuery) -> dict[str, Any]:
    entry = {
        "status": bundle.status,
        "cache_status": bundle.cache_status,
        "repository": query.repository,
        "work_item": query.work_item,
        "head_sha": query.head_sha,
        "role": query.role,
        "source_counts": dict(bundle.source_counts),
        "hit_refs": [hit.ref for hit in bundle.hits],
        "total_chars": bundle.total_chars,
        "saved_source_bytes": bundle.saved_source_bytes,
        "saved_input_tokens": bundle.saved_input_tokens,
    }
    trace.trace.setdefault("retrieval_operations", []).append(entry)
    trace.add_event(
        "hybrid_retrieval",
        {
            "status": bundle.status,
            "cache_status": bundle.cache_status,
            "source_counts": dict(bundle.source_counts),
            "result_count": len(bundle.hits),
            "total_chars": bundle.total_chars,
        },
    )
    return entry
