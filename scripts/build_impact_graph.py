#!/usr/bin/env python3
"""Build a deterministic Python import impact graph from the AI index."""

from __future__ import annotations

import argparse
from collections import defaultdict, deque
import json
from pathlib import Path
import subprocess
from typing import Any


def _module_candidates(module: str) -> list[str]:
    value = module.lstrip(".")
    if not value:
        return []
    parts = value.split(".")
    return [".".join(parts[:i]) for i in range(len(parts), 0, -1)]


def _changed_files(root: Path, base: str) -> list[str]:
    completed = subprocess.run(
        ["git", "-C", str(root), "diff", "--name-only", f"{base}...HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return sorted({line.strip() for line in completed.stdout.splitlines() if line.strip()})


def build_graph(index: dict[str, Any]) -> dict[str, Any]:
    files = index.get("files", [])
    module_to_path = {
        item.get("module"): item.get("path")
        for item in files
        if item.get("module") and item.get("path")
    }
    path_to_module = {path: module for module, path in module_to_path.items()}

    imports_by_path: dict[str, set[str]] = defaultdict(set)
    reverse: dict[str, set[str]] = defaultdict(set)

    for item in files:
        source_path = item.get("path")
        if not source_path:
            continue
        for imported in item.get("imports", []):
            raw_module = str(imported.get("module") or "")
            target_path = None
            for candidate in _module_candidates(raw_module):
                if candidate in module_to_path:
                    target_path = module_to_path[candidate]
                    break
            if not target_path or target_path == source_path:
                continue
            imports_by_path[source_path].add(target_path)
            reverse[target_path].add(source_path)

    tests = {
        item.get("path")
        for item in files
        if item.get("is_test") and item.get("path")
    }

    return {
        "schema_version": 1,
        "imports": {k: sorted(v) for k, v in sorted(imports_by_path.items())},
        "reverse_imports": {k: sorted(v) for k, v in sorted(reverse.items())},
        "tests": sorted(tests),
        "path_to_module": dict(sorted(path_to_module.items())),
    }


def impact_for_paths(graph: dict[str, Any], changed: list[str]) -> dict[str, Any]:
    reverse = {k: set(v) for k, v in graph.get("reverse_imports", {}).items()}
    tests = set(graph.get("tests", []))

    queue = deque(path for path in changed if path.endswith(".py"))
    seen = set(queue)
    affected = set(queue)

    while queue:
        current = queue.popleft()
        for dependant in reverse.get(current, set()):
            if dependant in seen:
                continue
            seen.add(dependant)
            affected.add(dependant)
            queue.append(dependant)

    affected_tests = sorted(path for path in affected if path in tests)
    affected_modules = sorted(path for path in affected if path not in tests)

    return {
        "changed_files": sorted(changed),
        "affected_python_files": sorted(affected),
        "affected_modules": affected_modules,
        "affected_tests": affected_tests,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", default="code-intelligence/ai-index.json")
    parser.add_argument("--base", default="HEAD^")
    parser.add_argument("--changed", nargs="*")
    parser.add_argument("--output", default="code-intelligence/impact.json")
    args = parser.parse_args()

    index = json.loads(Path(args.index).read_text(encoding="utf-8"))
    root = Path(".").resolve()
    graph = build_graph(index)
    changed = sorted(set(args.changed or _changed_files(root, args.base)))
    result = {
        "graph": graph,
        "impact": impact_for_paths(graph, changed),
    }

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "changed": len(result["impact"]["changed_files"]),
                "affected_python_files": len(result["impact"]["affected_python_files"]),
                "affected_tests": len(result["impact"]["affected_tests"]),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
