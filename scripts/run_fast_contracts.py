#!/usr/bin/env python3
"""Dependency-free, fail-closed CI contract lane with a two-second test budget."""

from __future__ import annotations

import os
from pathlib import Path
import sys
from time import perf_counter
import unittest

MAX_TEST_SECONDS = 2.0
MIN_EXPECTED_TESTS = 10
TEST_MODULE = "tests.test_ci_fast_contracts"


def main() -> int:
    # Script invocation adds scripts/ to sys.path, not the repository root.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    started = perf_counter()
    suite = unittest.defaultTestLoader.loadTestsFromName(TEST_MODULE)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    elapsed = perf_counter() - started

    print(
        f"Fast isolated contracts: {result.testsRun} tests in {elapsed:.3f}s "
        f"(budget {MAX_TEST_SECONDS:.1f}s)",
        flush=True,
    )
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with Path(summary_path).open("a", encoding="utf-8") as summary:
            summary.write(
                f"- Fast isolated contracts: {result.testsRun} tests, "
                f"{elapsed:.3f}s / {MAX_TEST_SECONDS:.1f}s budget, "
                f"{'PASS' if result.wasSuccessful() else 'FAIL'}\n"
            )

    if result.testsRun < MIN_EXPECTED_TESTS:
        print("Missing fast contract tests; failing closed.", file=sys.stderr)
        return 1
    if elapsed > MAX_TEST_SECONDS:
        print("Fast contract test budget exceeded.", file=sys.stderr)
        return 1
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
