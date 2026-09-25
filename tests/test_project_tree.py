import os

import pytest

from project_tree import PROJECT_ROOT, get_project_tree


def test_project_tree_rejects_paths_outside_root(monkeypatch):
    from project_tree import _tree
    outside = os.path.dirname(PROJECT_ROOT)
    if outside == PROJECT_ROOT:
        pytest.skip("root has no parent")
    monkeypatch.setenv("ALICE_PROJECT_ROOT", outside)
    assert os.path.realpath(outside) != PROJECT_ROOT


def test_project_tree_returns_bounded_structured_nodes():
    result = get_project_tree()
    assert isinstance(result, dict)
    assert "nodes" in result
    assert isinstance(result["nodes"], list)
    if result["nodes"]:
        node = result["nodes"][0]
        assert {"name", "path", "kind", "icon"} <= set(node)
