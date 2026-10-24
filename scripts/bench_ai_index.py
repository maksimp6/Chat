#!/usr/bin/env python3
"""Benchmark verified Python index builds and queries on fixed local Git fixtures."""

from __future__ import annotations
from core.env import env_snapshot

import argparse
from collections.abc import Callable
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build_ai_index import build_index, query_affected

FIXTURE_REPOSITORY = "fixture/index-benchmark"
FIXTURE_SOURCES = {
    "pkg/__init__.py": "def package_marker():\n    return 'synthetic'\n",
    "pkg/helpers.py": "def helper(value):\n    return value + 1\n",
    "pkg/service.py": "from pkg.helpers import helper\n\ndef run(value):\n    return helper(value)\n",
    "pkg/facade.py": "from pkg.service import run\n\ndef execute():\n    return run(1)\n",
    "pkg/unrelated.py": "def unrelated():\n    return 42\n",
    "tests/test_helpers.py": "from pkg.helpers import helper\n\ndef test_helper():\n    assert helper(1) == 2\n",
    "tests/test_service.py": "from pkg.service import run\n\ndef test_run():\n    assert run(1) == 2\n",
    "tests/test_facade.py": "from pkg.facade import execute\n\ndef test_execute():\n    assert execute() == 2\n",
}


def _json_bytes(value: Any) -> bytes:
    """Use one canonical serialization for equivalence, size and fixture hashes."""
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _git(root: Path, *arguments: str) -> str:
    """Run bounded local Git with fixed identity, dates and isolated configuration."""
    environment = {
        **env_snapshot(),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_COUNT": "0",
        "GIT_AUTHOR_DATE": "2000-01-01T00:00:00+00:00",
        "GIT_COMMITTER_DATE": "2000-01-01T00:00:00+00:00",
        "GIT_AUTHOR_NAME": "Index fixture",
        "GIT_COMMITTER_NAME": "Index fixture",
        "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
        "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
    }
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY"):
        environment.pop(name, None)
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return completed.stdout.strip()


def _fixture(root: Path) -> tuple[str, str]:
    """Create two eight-file commits, with one change, one addition and one deletion."""
    _git(root, "init", "-q", "--object-format=sha1", "--initial-branch=fixture")
    for name, source in FIXTURE_SOURCES.items():
        destination = root / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(source, encoding="utf-8", newline="\n")
    _git(root, "add", ".")
    _git(root, "-c", "core.hooksPath=/dev/null", "commit", "-qm", "fixture baseline")
    baseline = _git(root, "rev-parse", "HEAD")
    (root / "pkg/helpers.py").write_text(
        "def helper(value):\n    return value + 2\n", encoding="utf-8", newline="\n"
    )
    (root / "pkg/unrelated.py").unlink()
    (root / "pkg/added.py").write_text(
        "def added():\n    return 7\n", encoding="utf-8", newline="\n"
    )
    _git(root, "add", ".")
    _git(root, "-c", "core.hooksPath=/dev/null", "commit", "-qm", "fixture changes")
    return baseline, _git(root, "rev-parse", "HEAD")


def _measure(operation: Callable[[], Any], iterations: int) -> dict[str, int | float]:
    """Measure complete in-process operations, including Git calls for builds."""
    samples: list[float] = []
    for _ in range(iterations):
        start = time.perf_counter_ns()
        operation()
        samples.append((time.perf_counter_ns() - start) / 1_000_000)
    return {
        "iterations": iterations,
        "minimum_ms": min(samples),
        "median_ms": statistics.median(samples),
        "maximum_ms": max(samples),
    }


def _verify(full: Any, incremental: Any) -> None:
    """Reject invalid reuse before publishing performance evidence."""
    if full != incremental:
        raise ValueError("full and incremental indexes are not equivalent")


def _run(root: Path, iterations: int) -> dict[str, Any]:
    """Verify fixture results and measure rebuilds, cache reuse and repeated queries."""
    baseline, changed = _fixture(root)
    work: dict[str, dict[str, int]] = {name: {} for name in ("full", "warm", "changed")}

    def build(revision: str, previous: Any = None, stats: Any = None) -> dict[str, Any]:
        return build_index(
            root,
            revision=revision,
            repository=FIXTURE_REPOSITORY,
            previous_index=previous,
            stats=stats,
        )

    index = build(baseline, stats=work["full"])
    original = _json_bytes(index)
    _verify(index, build(baseline, index, work["warm"]))
    _verify(build(changed), build(changed, index, work["changed"]))
    paths = ["pkg/service.py"]
    expected = query_affected(index, paths)
    if (
        "pkg.service" not in expected["affected_modules"]
        or "tests/test_service.py" not in expected["affected_tests"]
        or expected["provenance"]["git_revision"] != baseline
    ):
        raise ValueError("fixture query is incomplete or has invalid provenance")

    def query() -> dict[str, Any]:
        result = query_affected(index, paths)
        if result != expected:
            raise ValueError("fixture query is not deterministic")
        return result

    timings = {
        "full_build": _measure(lambda: build(baseline), iterations),
        "warm_build": _measure(lambda: build(baseline, index), iterations),
        "changed_build": _measure(lambda: build(changed, index), iterations),
        "query": _measure(query, iterations),
    }
    if _json_bytes(index) != original or _git(root, "status", "--porcelain"):
        raise ValueError("benchmark mutated its inputs")
    return {
        "schema_version": 1,
        "fixture": {
            "repository": FIXTURE_REPOSITORY,
            "source_version": baseline,
            "changed_version": changed,
            "python_files": len(FIXTURE_SOURCES),
            "content_sha256": hashlib.sha256(_json_bytes(FIXTURE_SOURCES)).hexdigest(),
            "parser_version": index["parser_version"],
            "index_schema_version": index["schema_version"],
            "snapshot_schema_version": index["snapshot_schema_version"],
        },
        "verified": {
            "same_revision_equivalent": True,
            "changed_revision_equivalent": True,
            "query_deterministic": True,
            "inputs_unchanged": True,
        },
        "work": work,
        "timings": timings,
        "sizes": {
            "index_json_bytes": len(original),
            "query_json_bytes": len(_json_bytes(expected)),
        },
        "query": expected,
    }


def benchmark(*, iterations: int = 20, temporary_parent: Path | None = None) -> dict[str, Any]:
    """Run 1–100 iterations in an automatically removed synthetic repository."""
    if type(iterations) is not int or not 1 <= iterations <= 100:
        raise ValueError("iterations must be an integer between 1 and 100")
    with tempfile.TemporaryDirectory(prefix="logos-index-benchmark-", dir=temporary_parent) as name:
        return _run(Path(name), iterations)


def main() -> int:
    """Print a verified JSON report; failures never emit a success report."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=20)
    arguments = parser.parse_args()
    try:
        report = benchmark(iterations=arguments.iterations)
    except (ValueError, OSError, subprocess.SubprocessError):
        parser.exit(
            2, "index benchmark failed; check Git, temporary storage and iterations (1–100)\n"
        )
    print(json.dumps(report, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
