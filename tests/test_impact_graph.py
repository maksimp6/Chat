import json
from pathlib import Path

from scripts.build_impact_graph import build_graph, impact_for_paths


def test_impact_graph_finds_reverse_imports_and_tests():
    index = {
        "files": [
            {
                "path": "core.py",
                "module": "core",
                "is_test": False,
                "imports": [],
            },
            {
                "path": "service.py",
                "module": "service",
                "is_test": False,
                "imports": [{"module": "core", "name": None, "as": None}],
            },
            {
                "path": "tests/test_service.py",
                "module": "tests.test_service",
                "is_test": True,
                "imports": [{"module": "service", "name": None, "as": None}],
            },
        ]
    }

    graph = build_graph(index)
    result = impact_for_paths(graph, ["core.py"])

    assert graph["reverse_imports"]["core.py"] == ["service.py"]
    assert result["affected_modules"] == ["core.py", "service.py"]
    assert result["affected_tests"] == ["tests/test_service.py"]


def test_impact_graph_is_deterministic_for_unrelated_changes():
    index = {
        "files": [
            {
                "path": "a.py",
                "module": "a",
                "is_test": False,
                "imports": [],
            }
        ]
    }
    graph = build_graph(index)
    result = impact_for_paths(graph, ["README.md"])

    assert result == {
        "changed_files": ["README.md"],
        "affected_python_files": [],
        "affected_modules": [],
        "affected_tests": [],
    }
