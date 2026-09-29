import pytest

from browser.orchestration import BrowserDagBudget, BrowserRoleDag, BrowserRoleTask
from browser.pipeline import (
    BrowserSynthesisPolicy,
    SemanticBrowserWorker,
    synthesize_browser_dag,
)


def task(
    task_id,
    *,
    source=None,
    confidence=None,
    diff_from=None,
    max_chars=None,
    semantic_mode=None,
):
    metadata = {}
    if source is not None:
        metadata["source"] = source
    if confidence is not None:
        metadata["confidence"] = confidence
    if diff_from is not None:
        metadata["diff_from"] = diff_from
    if max_chars is not None:
        metadata["max_chars"] = max_chars
    if semantic_mode is not None:
        metadata["semantic_mode"] = semantic_mode
    return BrowserRoleTask(
        task_id=task_id,
        role="observer",
        session_id=task_id,
        capability="browser_cloud",
        action="inspect",
        target="page",
        metadata=metadata,
    )


def semantic_worker(payloads):
    def raw(item):
        payload = payloads[item.task_id]
        if callable(payload):
            return payload(item)
        return payload

    return SemanticBrowserWorker(raw)


def test_parallel_snapshots_merge_agreeing_evidence_without_escalation():
    dag = BrowserRoleDag(
        [
            task("shop-a", source="shop-a", confidence=0.8),
            task("shop-b", source="shop-b", confidence=0.9),
        ],
        semantic_worker(
            {
                "shop-a": {
                    "success": True,
                    "data": {
                        "version": "a1",
                        "nodes": [{"id": "buy", "role": "button", "name": "Buy"}],
                        "facts": {"price": 29990, "stock": True},
                    },
                    "metadata": {"consumed_tokens": 3},
                },
                "shop-b": {
                    "success": True,
                    "data": {
                        "version": "b1",
                        "nodes": [{"id": "buy", "role": "button", "name": "Buy"}],
                        "facts": {"price": 29990},
                    },
                    "usage": {"total_tokens": 4},
                },
            }
        ),
        budget=BrowserDagBudget(max_parallel_workers=2),
    )

    result = dag.run()
    synthesis = synthesize_browser_dag(dag, result)

    assert synthesis.status == "succeeded"
    assert synthesis.evidence.facts == {"price": 29990, "stock": True}
    assert synthesis.evidence.conflicts == {}
    assert synthesis.evidence.sources["price"] == ("shop-a", "shop-b")
    assert synthesis.consumed_tokens == 7
    assert synthesis.escalation.required is False
    assert synthesis.escalation.suggested_tier == "deterministic"


def test_conflicting_parallel_evidence_escalates_strong():
    dag = BrowserRoleDag(
        [task("shop-a"), task("shop-b")],
        semantic_worker(
            {
                "shop-a": {"success": True, "data": {"facts": {"price": 29990}}},
                "shop-b": {"success": True, "data": {"facts": {"price": 28990}}},
            }
        ),
        budget=BrowserDagBudget(max_parallel_workers=2),
    )

    synthesis = synthesize_browser_dag(dag, dag.run())

    assert synthesis.evidence.facts == {}
    assert "price" in synthesis.evidence.conflicts
    assert synthesis.escalation.required is True
    assert synthesis.escalation.suggested_tier == "strong"
    assert synthesis.escalation.reasons == ("evidence_conflicts:price",)


def test_truncated_snapshot_uses_cheap_escalation():
    dag = BrowserRoleDag(
        [task("large", max_chars=128)],
        semantic_worker(
            {
                "large": {
                    "success": True,
                    "data": {
                        "nodes": [
                            {
                                "id": f"n{i}",
                                "role": "button",
                                "name": "x" * 100,
                            }
                            for i in range(20)
                        ]
                    },
                }
            }
        ),
    )

    synthesis = synthesize_browser_dag(dag, dag.run())

    assert synthesis.snapshots["large"].truncated is True
    assert synthesis.escalation.required is True
    assert synthesis.escalation.suggested_tier == "cheap"
    assert synthesis.escalation.reasons == ("truncated_state:large",)


def test_policy_can_ignore_truncation():
    dag = BrowserRoleDag(
        [task("large", max_chars=128)],
        semantic_worker(
            {
                "large": {
                    "success": True,
                    "data": {
                        "nodes": [
                            {"id": str(i), "role": "button", "name": "x" * 100} for i in range(10)
                        ]
                    },
                }
            }
        ),
    )

    synthesis = synthesize_browser_dag(
        dag,
        dag.run(),
        policy=BrowserSynthesisPolicy(escalate_on_truncation=False),
    )

    assert synthesis.snapshots["large"].truncated is True
    assert synthesis.escalation.required is False
    assert synthesis.escalation.suggested_tier == "deterministic"


def test_diff_from_builds_compact_state_change():
    dag = BrowserRoleDag(
        [
            task("before"),
            task("after", diff_from="before"),
        ],
        semantic_worker(
            {
                "before": {
                    "success": True,
                    "data": {
                        "version": "v1",
                        "nodes": [{"id": "price", "role": "generic", "value": "30"}],
                        "facts": {"price": 30},
                    },
                },
                "after": {
                    "success": True,
                    "data": {
                        "version": "v2",
                        "nodes": [{"id": "price", "role": "generic", "value": "29"}],
                        "facts": {"price": 29},
                    },
                },
            }
        ),
    )

    synthesis = synthesize_browser_dag(dag, dag.run())

    diff = synthesis.diffs["after"]
    assert diff.from_version == "v1"
    assert diff.to_version == "v2"
    assert diff.changed_facts == {"price": {"before": 30, "after": 29}}
    assert synthesis.escalation.suggested_tier == "strong"


def test_missing_diff_source_escalates_strong():
    dag = BrowserRoleDag(
        [task("after", diff_from="missing")],
        semantic_worker({"after": {"success": True, "data": {"facts": {"stock": True}}}}),
    )

    synthesis = synthesize_browser_dag(dag, dag.run())

    assert synthesis.diffs == {}
    assert synthesis.escalation.required is True
    assert synthesis.escalation.suggested_tier == "strong"
    assert synthesis.escalation.reasons == ("missing_diff_sources:after<-missing",)


def test_failed_task_escalates_strong():
    dag = BrowserRoleDag(
        [task("good"), task("bad")],
        semantic_worker(
            {
                "good": {"success": True, "data": {"facts": {"stock": True}}},
                "bad": {"success": False, "error": "boom"},
            }
        ),
        budget=BrowserDagBudget(max_parallel_workers=2),
    )

    synthesis = synthesize_browser_dag(dag, dag.run())

    assert synthesis.task_statuses == {"bad": "failed", "good": "succeeded"}
    assert synthesis.escalation.required is True
    assert synthesis.escalation.suggested_tier == "strong"
    assert synthesis.escalation.reasons[0] == "task_failures:bad"


def test_semantic_worker_passthrough_mode_keeps_raw_result():
    raw = {"success": True, "data": {"raw": True}}
    wrapped = SemanticBrowserWorker(lambda _task: raw)
    item = task("raw", semantic_mode="passthrough")

    assert wrapped(item) is raw


def test_semantic_worker_rejects_invalid_modes_and_data():
    bad_mode = SemanticBrowserWorker(lambda _task: {"success": True, "data": {}})
    with pytest.raises(ValueError, match="unsupported semantic_mode"):
        bad_mode(task("bad-mode", semantic_mode="mystery"))

    bad_data = SemanticBrowserWorker(lambda _task: {"success": True, "data": "text"})
    with pytest.raises(ValueError, match="must be a mapping"):
        bad_data(task("bad-data"))

    with pytest.raises(TypeError, match="worker must be callable"):
        SemanticBrowserWorker(None)


def test_synthesis_mapping_is_compact_and_serializable():
    dag = BrowserRoleDag(
        [task("shop")],
        semantic_worker(
            {
                "shop": {
                    "success": True,
                    "data": {
                        "url": "https://example.com",
                        "facts": {"price": 10},
                    },
                }
            }
        ),
    )

    mapped = synthesize_browser_dag(dag, dag.run()).to_mapping()

    assert mapped["task_statuses"] == {"shop": "succeeded"}
    assert mapped["snapshots"]["shop"]["facts"] == {"price": 10}
    assert mapped["evidence"]["facts"] == {"price": 10}
    assert mapped["escalation"]["suggested_tier"] == "deterministic"


@pytest.mark.parametrize(
    ("call", "message"),
    [
        (
            lambda: BrowserSynthesisPolicy(max_snapshots=0),
            "max_snapshots must be >= 1",
        ),
        (
            lambda: BrowserSynthesisPolicy(max_diffs=0),
            "max_diffs must be >= 1",
        ),
        (
            lambda: BrowserSynthesisPolicy(max_evidence_fields=0),
            "max_evidence_fields must be >= 1",
        ),
        (
            lambda: BrowserSynthesisPolicy(max_conflicts_per_field=0),
            "max_conflicts_per_field must be >= 1",
        ),
        (
            lambda: synthesize_browser_dag(None, None),
            "dag must be a BrowserRoleDag",
        ),
    ],
)
def test_synthesis_validation(call, message):
    with pytest.raises((TypeError, ValueError), match=message):
        call()
