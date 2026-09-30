import sqlite3

import pytest

from agent_context import CacheLookup, ContextSlice, EvidenceVersion, TaskPacket, TaskScope
from agent_memory import AgentMemoryStore, MemoryRecord
from agent_retrieval import (
    HybridRetriever,
    RetrievalBundle,
    RetrievalHit,
    RetrievalQuery,
    record_retrieval,
)
from agent_retrieval.service import _bm25_scores, _bound_hits, _cache_matches_query
from trace_manager import ExecutionTrace


HEAD = "head-123"


def _store(tmp_path):
    path = tmp_path / "retrieval.db"

    def connect():
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        return conn

    return AgentMemoryStore(connect)


def _query(**overrides):
    values = {
        "text": "settle payment invoice",
        "repository": "maksimp6/Chat",
        "work_item": "PR#900",
        "branch": "feat/retrieval",
        "head_sha": HEAD,
        "role": "backend-engineer",
        "skills_version": "skills-v1",
        "selected_skills": ("github-ci-diagnosis",),
        "max_results": 8,
        "max_chars": 6000,
    }
    values.update(overrides)
    return RetrievalQuery(**values)


def _memory(
    *,
    memory_id,
    kind,
    text,
    head=HEAD,
    work_item="PR#900",
    visibility=("backend-engineer",),
    scope="repo:maksimp6/Chat",
):
    provenance = {
        "source_type": "github",
        "repository": "maksimp6/Chat",
        "refs": [f"memory:{memory_id}"],
    }
    if kind == "task":
        provenance.update(
            {
                "work_item": work_item,
                "branch": "feat/retrieval",
                "head_sha": head,
                "skills_version": "skills-v1",
            }
        )
    return MemoryRecord.create(
        memory_id=memory_id,
        kind=kind,
        scope=scope,
        text=text,
        provenance=provenance,
        source_version=head,
        payload={"summary": text},
        visibility=visibility,
        now=100,
    )


def _index():
    return {
        "files": [
            {
                "path": "billing/service.py",
                "module": "billing.service",
                "sha256": "a" * 64,
                "is_test": False,
                "symbols": [
                    {
                        "name": "PaymentService",
                        "qualified_name": "PaymentService",
                        "kind": "class",
                        "line": 1,
                        "end_line": 10,
                    },
                    {
                        "name": "settle_invoice",
                        "qualified_name": "PaymentService.settle_invoice",
                        "kind": "method",
                        "line": 3,
                        "end_line": 8,
                    },
                ],
                "imports": [],
                "calls": ["ledger.settle"],
            },
            {
                "path": "browser/semantic.py",
                "module": "browser.semantic",
                "sha256": "b" * 64,
                "is_test": False,
                "symbols": [
                    {
                        "name": "SemanticSnapshot",
                        "qualified_name": "SemanticSnapshot",
                        "kind": "class",
                        "line": 1,
                        "end_line": 5,
                    }
                ],
                "imports": [],
                "calls": [],
            },
        ],
        "tests_by_module": {
            "billing.service": ["tests/test_billing.py"],
        },
    }


def _cache(status="hit"):
    scope = TaskScope(
        repository="maksimp6/Chat",
        work_item="PR#900",
        base_sha="base",
        head_sha=HEAD,
        role="backend-engineer",
        selected_skills=("github-ci-diagnosis",),
    )
    packet = TaskPacket(
        scope=scope,
        evidence=EvidenceVersion(task="task-1", ci="ci-1", skills="skills-v1"),
        objective="settle payment invoice",
        expected_deliverable="fix",
        slices=(
            ContextSlice(
                name="billing",
                payload={"summary": "settle payment invoice cache evidence"},
                depends_on=("task",),
                source_bytes=500,
                input_tokens=100,
            ),
        ),
    )
    return CacheLookup(
        status=status,
        cache_key=packet.cache_key(),
        packet=packet if status == "hit" else None,
        scope=scope,
        evidence=packet.evidence,
        reusable_slices=packet.slices,
        stale_components=() if status == "hit" else ("ci",),
        saved_source_bytes=500,
        saved_input_tokens=100,
    )


def test_query_contract_is_bounded():
    with pytest.raises(ValueError):
        _query(text="")
    with pytest.raises(ValueError, match="max_results"):
        _query(max_results=20)
    with pytest.raises(ValueError, match="max_chars"):
        _query(max_chars=100)


def test_query_and_result_contract_edge_cases_are_explicit():
    with pytest.raises(ValueError, match="repository is required"):
        _query(repository=None)

    with pytest.raises(ValueError, match="source_type is required"):
        RetrievalHit(source_type="", ref="ref", score=1, text="text")

    with pytest.raises(ValueError, match="retrieval status"):
        RetrievalBundle(status="unknown")

    with pytest.raises(ValueError, match="unsupported cache status"):
        RetrievalBundle(status="miss", cache_status="stale")

    hit = RetrievalHit(
        source_type="memory",
        ref="memory:test",
        score=1,
        text="evidence",
        metadata={"nested": ["value"]},
    )
    bundle = RetrievalBundle(
        status="hit",
        hits=(hit,),
        source_counts={"memory": 1},
        total_chars=len(hit.text),
    )
    assert bundle.as_dict()["hits"][0]["metadata"] == {"nested": ["value"]}


def test_private_retrieval_guards_cover_defensive_paths():
    assert _bm25_scores("=", ["settle invoice"]) == [0.0]

    lookup = _cache("hit")
    lookup_without_scope = CacheLookup(
        status=lookup.status,
        cache_key=lookup.cache_key,
        packet=lookup.packet,
        scope=None,
        evidence=lookup.evidence,
        reusable_slices=lookup.reusable_slices,
        stale_components=lookup.stale_components,
        saved_source_bytes=lookup.saved_source_bytes,
        saved_input_tokens=lookup.saved_input_tokens,
    )
    assert not _cache_matches_query(lookup_without_scope, _query())

    class EmptyHit:
        score = 1.0
        source_type = "test"
        ref = "empty"
        text = ""

    assert _bound_hits([EmptyHit()], _query()) == ()


def test_exact_cache_hit_short_circuits_memory_and_code(tmp_path):
    store = _store(tmp_path)
    store.upsert(
        _memory(
            memory_id="project",
            kind="project",
            text="settle payment invoice project memory",
        )
    )
    retriever = HybridRetriever(
        memory_store=store,
        repository_index=_index(),
        repository_index_version=HEAD,
        repository_index_repository="maksimp6/Chat",
    )

    bundle = retriever.retrieve(_query(), cache_lookup=_cache("hit"), now=101)

    assert bundle.status == "hit"
    assert bundle.cache_status == "hit"
    assert bundle.source_counts == {"cache": 1}
    assert [hit.source_type for hit in bundle.hits] == ["cache"]
    assert bundle.saved_input_tokens == 100


def test_cache_provenance_must_match_retrieval_query(tmp_path):
    store = _store(tmp_path)
    retriever = HybridRetriever(memory_store=store)
    mismatched = _cache("hit")

    bundle = retriever.retrieve(
        _query(head_sha="different-head"),
        cache_lookup=mismatched,
        now=101,
    )

    assert bundle.status == "miss"
    assert bundle.cache_status == "miss"
    assert bundle.hits == ()
    assert bundle.saved_source_bytes == 0
    assert bundle.saved_input_tokens == 0


def test_partial_cache_continues_into_memory_and_code(tmp_path):
    store = _store(tmp_path)
    store.upsert(
        _memory(
            memory_id="task",
            kind="task",
            text="settle payment invoice task decision",
        )
    )
    retriever = HybridRetriever(
        memory_store=store,
        repository_index=_index(),
        repository_index_version=HEAD,
        repository_index_repository="maksimp6/Chat",
    )

    bundle = retriever.retrieve(_query(), cache_lookup=_cache("partial"), now=101)

    assert bundle.status == "hit"
    assert bundle.cache_status == "partial"
    assert bundle.source_counts["cache"] == 1
    assert bundle.source_counts["memory"] >= 1
    assert bundle.source_counts["code"] >= 1


def test_task_memory_requires_branch_and_skill_version(tmp_path):
    store = _store(tmp_path)
    wrong_branch = _memory(
        memory_id="wrong-branch",
        kind="task",
        text="settle payment invoice wrong branch",
    )
    wrong_branch_payload = wrong_branch.as_dict()
    wrong_branch_payload["provenance"]["branch"] = "other"
    store.upsert(MemoryRecord(**wrong_branch_payload))

    wrong_skills = _memory(
        memory_id="wrong-skills",
        kind="task",
        text="settle payment invoice wrong skills",
    )
    wrong_skills_payload = wrong_skills.as_dict()
    wrong_skills_payload["provenance"]["skills_version"] = "skills-v2"
    store.upsert(MemoryRecord(**wrong_skills_payload))

    retriever = HybridRetriever(memory_store=store)
    refs = [hit.ref for hit in retriever.retrieve(_query(), now=101).hits]

    assert "memory:wrong-branch" not in refs
    assert "memory:wrong-skills" not in refs


def test_task_memory_source_version_must_match_head(tmp_path):
    store = _store(tmp_path)
    record = _memory(
        memory_id="wrong-source-version",
        kind="task",
        text="settle payment invoice stale source version",
    )
    payload = record.as_dict()
    payload["source_version"] = "old-head"
    store.upsert(MemoryRecord(**payload))

    bundle = HybridRetriever(memory_store=store).retrieve(_query(), now=101)

    assert "memory:wrong-source-version" not in [hit.ref for hit in bundle.hits]


def test_github_memory_from_other_repository_is_not_returned(tmp_path):
    store = _store(tmp_path)
    record = _memory(
        memory_id="other-repository",
        kind="project",
        text="settle payment invoice foreign repository",
        visibility=(),
    )
    payload = record.as_dict()
    payload["provenance"]["repository"] = "other/repo"
    store.upsert(MemoryRecord(**payload))

    bundle = HybridRetriever(memory_store=store).retrieve(_query(), now=101)

    assert "memory:other-repository" not in [hit.ref for hit in bundle.hits]


def test_stale_or_wrong_task_memory_is_not_returned(tmp_path):
    store = _store(tmp_path)
    store.upsert(
        _memory(
            memory_id="old-head",
            kind="task",
            text="settle payment invoice stale head",
            head="old-head",
        )
    )
    store.upsert(
        _memory(
            memory_id="wrong-pr",
            kind="task",
            text="settle payment invoice other task",
            work_item="PR#901",
        )
    )
    store.upsert(
        _memory(
            memory_id="project",
            kind="project",
            text="settle payment invoice accepted project fact",
        )
    )
    retriever = HybridRetriever(memory_store=store)

    bundle = retriever.retrieve(_query(), now=101)
    refs = [hit.ref for hit in bundle.hits]

    assert "memory:old-head" not in refs
    assert "memory:wrong-pr" not in refs
    assert "memory:project" in refs


def test_visibility_is_enforced_by_memory_store(tmp_path):
    store = _store(tmp_path)
    store.upsert(
        _memory(
            memory_id="security",
            kind="project",
            text="settle payment invoice secret architecture",
            visibility=("security-reviewer",),
        )
    )
    retriever = HybridRetriever(memory_store=store)

    assert retriever.retrieve(_query(), now=101).status == "miss"
    visible = retriever.retrieve(_query(role="security-reviewer"), now=101)
    assert visible.status == "hit"
    assert visible.hits[0].ref == "memory:security"


def test_repository_index_must_match_exact_head(tmp_path):
    store = _store(tmp_path)
    stale = HybridRetriever(
        memory_store=store,
        repository_index=_index(),
        repository_index_version="old-head",
        repository_index_repository="maksimp6/Chat",
    )
    current = HybridRetriever(
        memory_store=store,
        repository_index=_index(),
        repository_index_version=HEAD,
        repository_index_repository="maksimp6/Chat",
    )

    wrong_repo = HybridRetriever(
        memory_store=store,
        repository_index=_index(),
        repository_index_version=HEAD,
        repository_index_repository="other/repo",
    )

    assert stale.retrieve(_query(), now=101).status == "miss"
    assert wrong_repo.retrieve(_query(), now=101).status == "miss"
    bundle = current.retrieve(_query(), now=101)
    assert bundle.status == "hit"
    assert bundle.hits[0].ref == "billing/service.py"
    assert "PaymentService.settle_invoice" in bundle.hits[0].text
    assert bundle.hits[0].as_dict()["metadata"]["tests"] == ["tests/test_billing.py"]


def test_results_are_bounded_by_count_and_characters(tmp_path):
    store = _store(tmp_path)
    for index in range(5):
        store.upsert(
            _memory(
                memory_id=f"m{index}",
                kind="project",
                text="settle payment invoice " + ("x" * 200),
                visibility=(),
            )
        )
    retriever = HybridRetriever(memory_store=store)

    bundle = retriever.retrieve(_query(max_results=2, max_chars=128), now=101)

    assert len(bundle.hits) <= 2
    assert bundle.total_chars <= 128


def test_non_task_github_memory_honors_declared_freshness_metadata(tmp_path):
    store = _store(tmp_path)
    current = _memory(
        memory_id="current-process",
        kind="process",
        text="settle payment invoice current process evidence",
        visibility=(),
    )
    current_payload = current.as_dict()
    current_payload["provenance"].update(
        {
            "work_item": "PR#900",
            "branch": "feat/retrieval",
            "head_sha": HEAD,
            "skills_version": "skills-v1",
        }
    )
    store.upsert(MemoryRecord(**current_payload))

    stale = _memory(
        memory_id="stale-process",
        kind="process",
        text="settle payment invoice stale process evidence",
        visibility=(),
    )
    stale_payload = stale.as_dict()
    stale_payload["provenance"].update(
        {
            "work_item": "PR#900",
            "branch": "feat/retrieval",
            "head_sha": "old-head",
            "skills_version": "skills-v1",
        }
    )
    store.upsert(MemoryRecord(**stale_payload))

    refs = [hit.ref for hit in HybridRetriever(memory_store=store).retrieve(_query(), now=101).hits]

    assert "memory:current-process" in refs
    assert "memory:stale-process" not in refs


def test_role_memory_only_matches_receiving_role(tmp_path):
    store = _store(tmp_path)
    store.upsert(
        _memory(
            memory_id="backend-role",
            kind="role",
            scope="role:backend-engineer",
            text="settle payment invoice backend procedure",
            visibility=(),
        )
    )
    store.upsert(
        _memory(
            memory_id="docs-role",
            kind="role",
            scope="role:docs-engineer",
            text="settle payment invoice docs procedure",
            visibility=(),
        )
    )
    retriever = HybridRetriever(memory_store=store)

    refs = [hit.ref for hit in retriever.retrieve(_query(), now=101).hits]

    assert "memory:backend-role" in refs
    assert "memory:docs-role" not in refs


def test_retrieval_telemetry_records_provenance_not_query_text(tmp_path):
    store = _store(tmp_path)
    store.upsert(
        _memory(
            memory_id="project",
            kind="project",
            text="settle payment invoice project memory",
            visibility=(),
        )
    )
    retriever = HybridRetriever(memory_store=store)
    query = _query(text="authorization: Bearer should-not-be-recorded")
    bundle = retriever.retrieve(query, now=101)
    trace = ExecutionTrace("trace-retrieval")

    entry = record_retrieval(trace, bundle, query)
    serialized = str(trace.finalize())

    assert entry["repository"] == "maksimp6/Chat"
    assert "authorization" not in serialized
    assert "should-not-be-recorded" not in serialized
    assert trace.trace["events"][-1]["type"] == "hybrid_retrieval"


def test_empty_sources_return_miss(tmp_path):
    bundle = HybridRetriever(memory_store=_store(tmp_path)).retrieve(_query(), now=101)

    assert bundle.status == "miss"
    assert bundle.hits == ()
