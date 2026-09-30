import pytest

from browser.orchestration import BrowserDagBudget, BrowserRoleDag, BrowserRoleTask
from browser.pipeline import (
    BrowserSynthesisPolicy,
    SemanticBrowserWorker,
    _copy_usage,
    _extract_snapshot,
    _raw_data,
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


def test_pipeline_private_edge_contracts_and_limits():
    assert _raw_data({"success": False, "error": "x"}) == {
        "success": False,
        "error": "x",
    }
    assert _raw_data({"success": True, "other": 1}) == {
        "success": True,
        "other": 1,
    }

    target = {}
    _copy_usage("plain", target)
    assert target == {}
    _copy_usage(
        {
            "metadata": {"consumed_tokens": 2},
            "usage": {"total_tokens": 3},
        },
        target,
    )
    assert target == {
        "metadata": {"consumed_tokens": 2},
        "usage": {"total_tokens": 3},
    }

    assert _extract_snapshot("bad") is None
    assert _extract_snapshot({"semantic_type": "other"}) is None
    assert _extract_snapshot({"semantic_type": "snapshot", "snapshot": "bad"}) is None

    dag = BrowserRoleDag(
        [
            task("a", confidence="not-a-number"),
            task("b", confidence=3),
            task("c", confidence=-2),
        ],
        semantic_worker(
            {
                "a": {"success": True, "data": {"facts": {"a": 1}}},
                "b": {"success": True, "data": {"facts": {"b": 2}}},
                "c": {"success": True, "data": {"facts": {"c": 3}}},
            }
        ),
    )
    synthesis = synthesize_browser_dag(
        dag,
        dag.run(),
        policy=BrowserSynthesisPolicy(max_snapshots=2),
    )
    assert set(synthesis.snapshots) == {"a", "b"}
    assert synthesis.evidence.facts == {"a": 1, "b": 2}

    diff_dag = BrowserRoleDag(
        [
            task("base"),
            task("one", diff_from="base"),
            task("two", diff_from="base"),
        ],
        semantic_worker(
            {
                "base": {"success": True, "data": {"version": "v0", "facts": {"x": 0}}},
                "one": {"success": True, "data": {"version": "v1", "facts": {"x": 1}}},
                "two": {"success": True, "data": {"version": "v2", "facts": {"x": 2}}},
            }
        ),
    )
    limited = synthesize_browser_dag(
        diff_dag,
        diff_dag.run(),
        policy=BrowserSynthesisPolicy(max_diffs=1),
    )
    assert len(limited.diffs) == 1


def test_synthesis_handles_external_result_task_without_definition():
    from browser.orchestration import BrowserDagResult, BrowserTaskResult

    dag = BrowserRoleDag([], lambda _task: None)
    external = BrowserDagResult(
        status="succeeded",
        tasks={
            "external": BrowserTaskResult(
                task_id="external",
                status="succeeded",
                role="observer",
                session_id="outside",
                data={
                    "semantic_type": "snapshot",
                    "snapshot": {
                        "url": None,
                        "title": None,
                        "version": None,
                        "nodes": [],
                        "facts": {"price": 10},
                        "truncated": False,
                    },
                },
            )
        },
        consumed_tokens=0,
        duration_ms=0,
    )

    synthesis = synthesize_browser_dag(dag, external)

    assert "external" in synthesis.snapshots
    assert synthesis.evidence.facts == {}


def test_remaining_synthesis_edge_contracts():
    malformed = _extract_snapshot(
        {
            "semantic_type": "snapshot",
            "snapshot": {
                "nodes": [],
                "facts": [],
            },
        }
    )
    assert malformed is not None
    assert malformed.facts == {}

    passthrough_dag = BrowserRoleDag(
        [task("raw", semantic_mode="passthrough")],
        SemanticBrowserWorker(lambda _task: {"success": True, "data": {"raw": True}}),
    )
    passthrough = synthesize_browser_dag(passthrough_dag, passthrough_dag.run())
    assert passthrough.snapshots == {}

    valid_dag = BrowserRoleDag([], lambda _task: None)
    with pytest.raises(TypeError, match="dag_result must be a BrowserDagResult"):
        synthesize_browser_dag(valid_dag, None)
