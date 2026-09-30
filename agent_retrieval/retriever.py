"""Deterministic BM25-style hybrid retrieval and telemetry."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import math
import re
from typing import Any, Iterable

from .models import RetrievalDocument, RetrievalHit, RetrievalQuery, RetrievalResult
from .sources import RetrievalSource


_TOKEN_RE = re.compile(r"[\w./:-]{2,}", re.UNICODE)


def _tokens(text: str) -> list[str]:
    return [token.lower() for token in _TOKEN_RE.findall(text)]


def _metadata_text(document: RetrievalDocument) -> str:
    values: list[str] = []
    for key in ("path", "module", "kind", "scope"):
        value = document.metadata.get(key)
        if value:
            values.append(str(value))
    symbols = document.metadata.get("symbols")
    if isinstance(symbols, (list, tuple)):
        values.extend(str(value) for value in symbols)
    return " ".join(values)


@dataclass(frozen=True)
class _ScoredDocument:
    document: RetrievalDocument
    score: float
    matched_terms: tuple[str, ...]


class HybridRetriever:
    """Rank already-normalized evidence without rereading raw repository content."""

    def __init__(self, sources: Iterable[RetrievalSource]) -> None:
        self._sources = tuple(sources)

    def retrieve(
        self,
        query: RetrievalQuery,
        *,
        preloaded: Iterable[RetrievalDocument] = (),
        source_reads_avoided: int = 0,
    ) -> RetrievalResult:
        selected_sources = [
            source
            for source in self._sources
            if not query.source_types or source.source_type in query.source_types
        ]

        documents = list(preloaded)
        stale_filtered = 0
        visibility_filtered = 0
        sensitive_filtered = 0
        for source in selected_sources:
            batch = source.collect(query)
            documents.extend(batch.documents)
            stale_filtered += batch.stale_filtered
            visibility_filtered += batch.visibility_filtered
            sensitive_filtered += batch.sensitive_filtered

        deduped: dict[tuple[str, str], RetrievalDocument] = {}
        for document in documents:
            key = (document.source_type, document.document_id)
            deduped[key] = document
        corpus = list(deduped.values())
        query_terms = tuple(dict.fromkeys(_tokens(query.text)))
        if not query_terms:
            raise ValueError("retrieval query has no searchable terms")

        scored = self._score(query_terms, corpus)
        hits: list[RetrievalHit] = []
        chars = 0
        truncated = False
        for item in scored:
            if len(hits) >= query.limit:
                truncated = True
                break
            document_chars = len(item.document.text)
            if hits and chars + document_chars > query.max_chars:
                truncated = True
                break
            if not hits and document_chars > query.max_chars:
                clipped = RetrievalDocument(
                    **{
                        **item.document.as_dict(),
                        "text": item.document.text[: query.max_chars],
                    }
                )
                item = _ScoredDocument(
                    document=clipped,
                    score=item.score,
                    matched_terms=item.matched_terms,
                )
                document_chars = len(clipped.text)
                truncated = True
            hits.append(
                RetrievalHit(
                    document=item.document,
                    score=item.score,
                    matched_terms=item.matched_terms,
                )
            )
            chars += document_chars

        return RetrievalResult(
            status="hit" if hits else "miss",
            hits=tuple(hits),
            documents_considered=len(corpus),
            stale_filtered=stale_filtered,
            visibility_filtered=visibility_filtered,
            sensitive_filtered=sensitive_filtered,
            source_reads_avoided=max(0, int(source_reads_avoided)),
            chars_returned=chars,
            truncated=truncated,
        )

    @staticmethod
    def _score(
        query_terms: tuple[str, ...],
        documents: list[RetrievalDocument],
    ) -> list[_ScoredDocument]:
        if not documents:
            return []
        tokenized = [_tokens(document.text) for document in documents]
        avg_length = sum(len(tokens) for tokens in tokenized) / max(1, len(tokenized))
        avg_length = max(1.0, avg_length)
        document_frequency = {
            term: sum(1 for tokens in tokenized if term in set(tokens))
            for term in query_terms
        }

        ranked: list[_ScoredDocument] = []
        for document, tokens in zip(documents, tokenized):
            counts = Counter(tokens)
            score = 0.0
            matched: list[str] = []
            for term in query_terms:
                frequency = counts.get(term, 0)
                if not frequency:
                    continue
                matched.append(term)
                df = document_frequency[term]
                idf = math.log(1.0 + (len(documents) - df + 0.5) / (df + 0.5))
                denominator = frequency + 1.2 * (
                    0.25 + 0.75 * len(tokens) / avg_length
                )
                score += idf * (frequency * 2.2) / denominator

            metadata_tokens = set(_tokens(_metadata_text(document)))
            metadata_matches = set(query_terms).intersection(metadata_tokens)
            if metadata_matches:
                score += 0.35 * len(metadata_matches)
                matched.extend(sorted(metadata_matches))

            if score > 0:
                ranked.append(
                    _ScoredDocument(
                        document=document,
                        score=score,
                        matched_terms=tuple(sorted(set(matched))),
                    )
                )

        return sorted(
            ranked,
            key=lambda item: (
                -item.score,
                item.document.source_type,
                item.document.document_id,
            ),
        )


def record_retrieval(
    trace: Any,
    result: RetrievalResult,
    *,
    query: RetrievalQuery,
) -> dict[str, Any]:
    payload = result.as_dict()
    entry = {
        "status": result.status,
        "repository": query.repository,
        "head_sha": query.head_sha,
        "role": query.role,
        "source_counts": payload["source_counts"],
        "documents_considered": result.documents_considered,
        "hits": len(result.hits),
        "chars_returned": result.chars_returned,
        "stale_filtered": result.stale_filtered,
        "visibility_filtered": result.visibility_filtered,
        "sensitive_filtered": result.sensitive_filtered,
        "source_reads_avoided": result.source_reads_avoided,
        "truncated": result.truncated,
    }
    trace.trace.setdefault("retrieval_operations", []).append(entry)
    trace.add_event(
        "agent_retrieval",
        {
            "status": result.status,
            "hits": len(result.hits),
            "chars_returned": result.chars_returned,
            "source_reads_avoided": result.source_reads_avoided,
            "stale_filtered": result.stale_filtered,
        },
    )
    return entry
