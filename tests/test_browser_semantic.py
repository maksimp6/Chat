import pytest

from browser.semantic import (
    Evidence,
    SemanticNode,
    SemanticSnapshot,
    build_semantic_snapshot,
    diff_semantic_snapshots,
    merge_evidence,
)


def test_semantic_snapshot_filters_to_interactive_nodes():
    snapshot = build_semantic_snapshot(
        {
            "url": "https://example.com/product",
            "title": "Product",
            "version": "v1",
            "nodes": [
                {"id": "h1", "role": "heading", "name": "Printer"},
                {"id": "buy", "role": "button", "name": "Buy"},
                {
                    "id": "price",
                    "role": "generic",
                    "name": "Price",
                    "states": ["focusable"],
                },
            ],
            "facts": {"price": 29990},
        },
        interactive_only=True,
    )

    assert [node.node_id for node in snapshot.nodes] == ["buy", "price"]
    assert snapshot.facts == {"price": 29990}
    assert snapshot.truncated is False


def test_semantic_snapshot_applies_node_budget_deterministically():
    snapshot = build_semantic_snapshot(
        {"nodes": [{"id": f"n{i}", "role": "button", "name": f"Button {i}"} for i in range(10)]},
        max_nodes=3,
    )

    assert [node.node_id for node in snapshot.nodes] == ["n0", "n1", "n2"]
    assert snapshot.truncated is True


def test_semantic_snapshot_applies_character_budget():
    snapshot = build_semantic_snapshot(
        {
            "url": "https://example.com",
            "nodes": [
                {
                    "id": f"n{i}",
                    "role": "button",
                    "name": "x" * 120,
                }
                for i in range(20)
            ],
            "facts": {f"fact-{i}": "y" * 80 for i in range(20)},
        },
        max_chars=300,
    )

    assert snapshot.truncated is True
    assert len(snapshot.to_mapping()["nodes"]) < 20


def test_semantic_node_normalizes_text_and_states():
    node = SemanticNode.from_mapping(
        {
            "node_id": "field",
            "role": "TextBox",
            "label": "  Email  ",
            "text": " value ",
            "states": ["focusable", "required", "focusable"],
        },
        index=0,
    )

    assert node.node_id == "field"
    assert node.role == "textbox"
    assert node.name == "Email"
    assert node.value == "value"
    assert node.states == ("focusable", "required")
    assert node.interactive is True


def test_semantic_diff_returns_only_changes():
    before = build_semantic_snapshot(
        {
            "version": "v1",
            "nodes": [
                {"id": "price", "role": "generic", "name": "Price", "value": "29990"},
                {"id": "buy", "role": "button", "name": "Buy"},
            ],
            "facts": {
                "price": 29990,
                "stock": True,
                "seller": "A",
            },
        }
    )
    after = build_semantic_snapshot(
        {
            "version": "v2",
            "nodes": [
                {"id": "price", "role": "generic", "name": "Price", "value": "28990"},
                {"id": "credit", "role": "button", "name": "Credit"},
            ],
            "facts": {
                "price": 28990,
                "stock": True,
                "delivery": "tomorrow",
            },
        }
    )

    diff = diff_semantic_snapshots(before, after)

    assert diff.from_version == "v1"
    assert diff.to_version == "v2"
    assert [node.node_id for node in diff.added_nodes] == ["credit"]
    assert diff.removed_node_ids == ("buy",)
    assert [node.node_id for node in diff.changed_nodes] == ["price"]
    assert diff.added_facts == {"delivery": "tomorrow"}
    assert diff.removed_facts == ("seller",)
    assert diff.changed_facts == {"price": {"before": 29990, "after": 28990}}


def test_equal_snapshots_have_empty_diff():
    snapshot = build_semantic_snapshot(
        {
            "version": "same",
            "nodes": [{"id": "a", "role": "link", "name": "Open"}],
            "facts": {"x": {"a": 1}},
        }
    )

    diff = diff_semantic_snapshots(snapshot, snapshot)

    assert diff.added_nodes == ()
    assert diff.removed_node_ids == ()
    assert diff.changed_nodes == ()
    assert diff.added_facts == {}
    assert diff.removed_facts == ()
    assert diff.changed_facts == {}


def test_evidence_merge_collapses_exact_agreement():
    result = merge_evidence(
        [
            Evidence("price", 29990, "shop-a", 0.8),
            Evidence("price", 29990, "shop-b", 0.9),
            Evidence("stock", True, "shop-a", 0.7),
        ]
    )

    assert result.facts == {"price": 29990, "stock": True}
    assert result.conflicts == {}
    assert result.sources == {
        "price": ("shop-a", "shop-b"),
        "stock": ("shop-a",),
    }


def test_evidence_merge_preserves_conflicts():
    result = merge_evidence(
        [
            Evidence("price", 29990, "shop-a", 0.8),
            Evidence("price", 28990, "shop-b", 0.95),
            Evidence("price", 30990, "shop-c", 0.7),
        ]
    )

    assert result.facts == {}
    assert [item["value"] for item in result.conflicts["price"]] == [
        28990,
        29990,
        30990,
    ]
    assert result.conflicts["price"][0]["sources"] == ["shop-b"]


def test_evidence_merge_bounds_fields_and_conflicts():
    evidence = [Evidence(f"field-{i}", i, f"source-{i}", 1.0) for i in range(10)]
    evidence.extend(Evidence("conflict", value, f"source-{value}", 0.5) for value in range(10))

    result = merge_evidence(
        evidence,
        max_fields=3,
        max_conflicts_per_field=2,
    )

    assert len(result.facts) + len(result.conflicts) <= 3
    if "conflict" in result.conflicts:
        assert len(result.conflicts["conflict"]) == 2


def test_mapping_contracts_are_compact_and_stable():
    node = SemanticNode("id", "button", "Buy", None, ("focusable",))
    snapshot = SemanticSnapshot(
        url="https://example.com",
        title="Example",
        version="1",
        nodes=(node,),
        facts={"price": 10},
    )

    mapped = snapshot.to_mapping()

    assert mapped["nodes"] == [
        {
            "id": "id",
            "role": "button",
            "name": "Buy",
            "states": ["focusable"],
        }
    ]
    assert mapped["facts"] == {"price": 10}


@pytest.mark.parametrize(
    ("call", "message"),
    [
        (lambda: build_semantic_snapshot([], max_nodes=1), "must be a mapping"),
        (
            lambda: build_semantic_snapshot({}, max_nodes=0),
            "max_nodes must be >= 1",
        ),
        (
            lambda: build_semantic_snapshot({}, max_chars=100),
            "max_chars must be >= 128",
        ),
        (
            lambda: build_semantic_snapshot({"nodes": "bad"}),
            "nodes must be an array",
        ),
        (
            lambda: build_semantic_snapshot({"facts": []}),
            "facts must be an object",
        ),
        (
            lambda: diff_semantic_snapshots({}, {}),
            "requires SemanticSnapshot",
        ),
        (
            lambda: merge_evidence([], max_fields=0),
            "max_fields must be >= 1",
        ),
        (
            lambda: merge_evidence([], max_conflicts_per_field=0),
            "max_conflicts_per_field must be >= 1",
        ),
        (
            lambda: Evidence("", 1, "source"),
            "field is required",
        ),
        (
            lambda: Evidence("price", 1, ""),
            "source is required",
        ),
        (
            lambda: Evidence("price", 1, "source", 1.1),
            "confidence must be between 0 and 1",
        ),
    ],
)
def test_validation(call, message):
    with pytest.raises((TypeError, ValueError), match=message):
        call()


def test_semantic_edge_normalization_and_mapping_contracts():
    empty = SemanticNode.from_mapping(
        {"id": "empty", "role": "generic", "name": "   ", "states": "focusable"},
        index=0,
    )
    assert empty.name is None
    assert empty.states == ("focusable",)

    long = SemanticNode.from_mapping(
        {"id": "long", "role": "generic", "name": "x" * 300},
        index=1,
    )
    assert long.name is not None
    assert len(long.name) == 240
    assert long.name.endswith("…")

    snapshot = build_semantic_snapshot(
        {
            "version": "v1",
            "nodes": [
                "ignore-me",
                {"id": "real", "role": "button", "name": "Real"},
            ],
        }
    )
    assert [node.node_id for node in snapshot.nodes] == ["real"]

    after = build_semantic_snapshot(
        {
            "version": "v2",
            "nodes": [{"id": "real", "role": "button", "name": "Updated"}],
            "facts": {"price": 10},
        }
    )
    diff = diff_semantic_snapshots(snapshot, after)
    mapped_diff = diff.to_mapping()
    assert mapped_diff["from_version"] == "v1"
    assert mapped_diff["to_version"] == "v2"
    assert mapped_diff["changed_nodes"][0]["name"] == "Updated"

    merged = merge_evidence(
        [
            Evidence("stock", True, "shop-a", 1.0),
            Evidence("price", 10, "shop-a", 0.8),
            Evidence("price", 11, "shop-b", 0.9),
        ]
    )
    mapped_merged = merged.to_mapping()
    assert mapped_merged["facts"] == {"stock": True}
    assert mapped_merged["sources"] == {"stock": ["shop-a"]}
    assert [item["value"] for item in mapped_merged["conflicts"]["price"]] == [11, 10]
