import json
import sqlite3

import pytest

from agent_memory import AgentMemoryStore, MemoryRecord
from agent_retrieval import (
    AiIndexSource,
    HybridRetriever,
    MemorySource,
    RetrievalDocument,
    RetrievalQuery,
    StaticSource,
    record_retrieval,
)
from scripts.build_ai_index import build_index
from trace_manager import ExecutionTrace


def _memory_store(tmp_path):
    path = tmp_path / "retrieval-memory.db"

    def connect():
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        return conn

    return AgentMemoryStore(connect)


def _query(**updates):
    values = {
        "text": "rollback timestamp sqlite",
        "repository": "maksimp6/Chat",
        "head_sha": "head-1",
        "role": "backend-engineer",
        "limit": 5,
        "max_chars": 4000,
    }
    values.update(updates)
    return RetrievalQuery(**values)


def test_query_and_document_contracts_are_bounded():
    with pytest.raises(ValueError):
        RetrievalQuery(text="", repository="repo", head_sha="sha")
    with pytest.raises(ValueError):
        _query(limit=0)
    with pytest.raises(ValueError):
        _query(max_chars=100)
    with pytest.raises(ValueError):
        RetrievalDocument(
            document_id="d",
            source_type="docs",
            text="text",
            refs=(),
            source_version="sha",
        )
    with pytest.raises(ValueError):
        RetrievalDocument(
            document_id="d",
            source_type="docs",
            text="text",
            refs=("ref",),
            source_version="sha",
            sensitivity="secret",
        )


def test_memory_source_filters_stale_visibility_and_sensitive(tmp_path):
    store = _memory_store(tmp_path)
    store.upsert(
        MemoryRecord.create(
            memory_id="fresh",
            kind="project",
            scope="repo",
            text="SQLite rollback timestamp parser",
            provenance={
                "source_type": "github",
                "repository": "maksimp6/Chat",
                "refs": ["PR#548"],
            },
            source_version="head-1",
            freshness={"invalidate_on": ["head"], "head_bound": True},
            visibility=("backend-engineer",),
            now=10,
        )
    )
    store.upsert(
        MemoryRecord.create(
            memory_id="stale",
            kind="task",
            scope="repo",
            text="stale rollback timestamp",
            provenance={
                "source_type": "github",
                "repository": "maksimp6/Chat",
                "refs": ["PR#547"],
            },
            source_version="old-head",
            freshness={"invalidate_on": ["head"], "head_bound": True},
            now=10,
        )
    )
    store.upsert(
        MemoryRecord.create(
            memory_id="hidden",
            kind="role",
            scope="repo",
            text="rollback hidden",
            provenance={
                "source_type": "github",
                "repository": "maksimp6/Chat",
                "refs": ["issue#1"],
            },
            source_version="policy-1",
            visibility=("security-reviewer",),
            now=10,
        )
    )
    store.upsert(
        MemoryRecord.create(
            memory_id="sensitive",
            kind="project",
            scope="repo",
            text="rollback sensitive",
            provenance={
                "source_type": "github",
                "repository": "maksimp6/Chat",
                "refs": ["issue#2"],
            },
            source_version="policy-2",
            sensitivity="sensitive",
            now=10,
        )
    )

    batch = MemorySource(store).collect(_query(memory_scope="repo"))

    assert [doc.document_id for doc in batch.documents] == ["memory:fresh"]
    assert batch.stale_filtered == 1
    assert batch.visibility_filtered == 1
    assert batch.sensitive_filtered == 1


def test_ai_index_source_reuses_existing_deterministic_index(tmp_path):
    (tmp_path / "service.py").write_text(
        "def parse_sqlite_timestamp(value):\n    return value\n",
        encoding="utf-8",
    )
    index = build_index(tmp_path)
    source = AiIndexSource(index, repository="maksimp6/Chat", head_sha="head-1")

    batch = source.collect(_query(text="parse_sqlite_timestamp"))

    assert len(batch.documents) == 1
    document = batch.documents[0]
    assert document.refs == ("service.py",)
    assert "parse_sqlite_timestamp" in document.text
    assert source.collect(_query(head_sha="head-2")).documents == ()
    assert source.collect(_query(head_sha="head-2")).stale_filtered == 1


def test_static_source_enforces_head_visibility_and_sensitivity():
    source = StaticSource(
        "github_summary",
        (
            RetrievalDocument(
                document_id="fresh",
                source_type="github_summary",
                text="rollback timestamp fixed",
                refs=("PR#548",),
                source_version="head-1",
                metadata={"repository": "maksimp6/Chat"},
                head_bound=True,
            ),
            RetrievalDocument(
                document_id="stale",
                source_type="github_summary",
                text="rollback old",
                refs=("PR#547",),
                source_version="old",
                metadata={"repository": "maksimp6/Chat"},
                head_bound=True,
            ),
            RetrievalDocument(
                document_id="hidden",
                source_type="github_summary",
                text="rollback private role",
                refs=("issue#1",),
                source_version="head-1",
                metadata={"repository": "maksimp6/Chat"},
                visibility=("security-reviewer",),
            ),
            RetrievalDocument(
                document_id="sensitive",
                source_type="github_summary",
                text="rollback sensitive",
                refs=("issue#2",),
                source_version="head-1",
                metadata={"repository": "maksimp6/Chat"},
                sensitivity="sensitive",
            ),
        ),
    )

    batch = source.collect(_query())

    assert [doc.document_id for doc in batch.documents] == ["fresh"]
    assert batch.stale_filtered == 1
    assert batch.visibility_filtered == 1
    assert batch.sensitive_filtered == 1


def test_hybrid_bm25_ranking_is_deterministic_and_attributed():
    source = StaticSource(
        "docs",
        (
            RetrievalDocument(
                document_id="sqlite",
                source_type="docs",
                text="SQLite rollback timestamp parser regression",
                refs=("docs/sqlite.md",),
                source_version="v1",
                metadata={"repository": "maksimp6/Chat", "path": "docs/sqlite.md"},
            ),
            RetrievalDocument(
                document_id="android",
                source_type="docs",
                text="Android packaging signing",
                refs=("docs/android.md",),
                source_version="v1",
                metadata={"repository": "maksimp6/Chat", "path": "docs/android.md"},
            ),
        ),
    )
    retriever = HybridRetriever((source,))

    first = retriever.retrieve(_query())
    second = retriever.retrieve(_query())

    assert first == second
    assert first.status == "hit"
    assert [hit.document.document_id for hit in first.hits] == ["sqlite"]
    assert first.hits[0].document.refs == ("docs/sqlite.md",)
    assert set(first.hits[0].matched_terms) >= {"rollback", "timestamp"}


def test_source_filter_and_no_match_are_explicit():
    source = StaticSource(
        "docs",
        (
            RetrievalDocument(
                document_id="d",
                source_type="docs",
                text="something unrelated",
                refs=("docs/a.md",),
                source_version="v1",
            ),
        ),
    )
    retriever = HybridRetriever((source,))

    result = retriever.retrieve(_query(text="rollback", source_types=("memory",)))

    assert result.status == "miss"
    assert result.documents_considered == 0


def test_result_budgets_limit_hits_and_chars():
    documents = tuple(
        RetrievalDocument(
            document_id=f"d{index}",
            source_type="docs",
            text=("rollback timestamp " * 30) + str(index),
            refs=(f"doc#{index}",),
            source_version="v1",
        )
        for index in range(3)
    )
    retriever = HybridRetriever((StaticSource("docs", documents),))

    limited = retriever.retrieve(_query(limit=1))
    clipped = retriever.retrieve(_query(limit=5, max_chars=256))

    assert len(limited.hits) == 1
    assert limited.truncated is True
    assert len(clipped.hits) == 1
    assert clipped.chars_returned == 256
    assert clipped.truncated is True


def test_preloaded_documents_deduplicate_and_telemetry_records_savings():
    document = RetrievalDocument(
        document_id="same",
        source_type="docs",
        text="rollback timestamp sqlite",
        refs=("docs/a.md",),
        source_version="v1",
    )
    source = StaticSource("docs", (document,))
    retriever = HybridRetriever((source,))

    result = retriever.retrieve(
        _query(),
        preloaded=(document,),
        source_reads_avoided=3,
    )
    trace = ExecutionTrace("trace-retrieval")
    entry = record_retrieval(trace, result, query=_query())

    assert result.documents_considered == 1
    assert result.source_reads_avoided == 3
    assert entry["source_reads_avoided"] == 3
    assert entry["source_counts"] == {"docs": 1}
    assert trace.trace["events"][-1]["type"] == "agent_retrieval"
    json.dumps(trace.finalize())


def test_query_without_searchable_terms_fails_closed():
    source = StaticSource("docs", ())
    with pytest.raises(ValueError, match="searchable"):
        HybridRetriever((source,)).retrieve(_query(text="! ?"))
