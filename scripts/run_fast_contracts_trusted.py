"""Trusted, standard-library-only fast Memory DB test executor.

Loads immutable test files by their exact paths, not through the mutable tests
package. Runs only the expected cases and rejects skips and test substitutions.
CI executes the executor from a pinned Git commit.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
from pathlib import Path
import sys
from time import perf_counter
import unittest

MAX_SECONDS = 2.0
CASES: dict[str, dict[str, tuple[str, ...]]] = {
    "tests/test_ci_fast_contracts.py": {
        "FastMemoryContracts": (
            "test_committed_values_survive_reopen_and_delete",
            "test_torn_final_journal_frame_is_recovered",
            "test_tampering_with_committed_value_fails_closed",
            "test_failed_fsync_never_publishes_in_memory_state",
        ),
        "FastRoutingContracts": (
            "test_unknown_paths_select_every_platform",
            "test_web_path_includes_backend_postgres_and_mcp",
            "test_selected_infrastructure_suite_cannot_be_skipped",
            "test_fast_contracts_are_required_even_on_docs_only_pr",
        ),
        "FastWorkflowContracts": (
            "test_fast_job_is_unconditional_and_has_no_dependency_install",
            "test_ci_required_needs_fast_contract_result",
        ),
    },
    "tests/test_memory_fast_core_contracts.py": {
        "FastCoreContracts": (
            "test_explicit_commit_and_reopen",
            "test_atomic_batch_reopen",
            "test_torn_tail_fails_closed_without_mutation",
            "test_failed_fsync_does_not_acknowledge_commit",
            "test_second_writer_rejected",
        ),
    },
}
EXPECTED_COUNT = sum(len(methods) for classes in CASES.values() for methods in classes.values())


def load_cases(root: Path) -> tuple[unittest.TestSuite, tuple[str, ...]]:
    """Import frozen source files directly; do not execute tests/__init__.py."""
    sys.path.insert(0, str(root))
    suite = unittest.TestSuite()
    identities: list[str] = []
    for index, (relative, classes) in enumerate(CASES.items()):
        source = root / relative
        module_name = f"_trusted_memory_contract_{index}"
        spec = importlib.util.spec_from_file_location(module_name, source)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"cannot load approved tests: {relative}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        for class_name, approved_methods in classes.items():
            candidate = getattr(module, class_name, None)
            if (
                not isinstance(candidate, type)
                or not issubclass(candidate, unittest.TestCase)
                or candidate.__module__ != module_name
            ):
                raise RuntimeError(f"invalid frozen test class: {relative}:{class_name}")
            actual_methods = {
                name
                for name in candidate.__dict__
                if name.startswith("test_") and callable(candidate.__dict__[name])
            }
            if actual_methods != set(approved_methods):
                raise RuntimeError(f"unexpected test methods: {relative}:{class_name}")
            for method_name in approved_methods:
                case = candidate(method_name)
                expected_id = f"{module_name}.{class_name}.{method_name}"
                if case.id() != expected_id:
                    raise RuntimeError(f"test identity mismatch: {relative}:{method_name}")
                identities.append(expected_id)
                suite.addTest(case)
    if len(identities) != EXPECTED_COUNT or len(set(identities)) != EXPECTED_COUNT:
        raise RuntimeError("approved Memory DB contract inventory mismatch")
    # A mutable tests package must not participate in module loading.
    if "tests" in sys.modules:
        raise RuntimeError("tests package import is forbidden in trusted runner")
    return suite, tuple(identities)


def accepted(result: unittest.TestResult, expected_count: int = EXPECTED_COUNT) -> bool:
    """Fail closed for missing, skipped, expected-failure or unexpected-success tests."""
    return (
        result.testsRun == expected_count
        and result.wasSuccessful()
        and not result.skipped
        and not result.expectedFailures
        and not result.unexpectedSuccesses
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    root = args.root.resolve()

    # Include imports and file integrity checks in the 2-second execution budget.
    started = perf_counter()
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from check_memory_contract_integrity import verify

    errors = verify(root)
    if errors:
        for error in errors:
            print(f"BLOCKED: {error}", file=sys.stderr)
        return 1

    try:
        suite, identities = load_cases(root)
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    except (
        AttributeError,
        ImportError,
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
    ) as exc:
        print(f"BLOCKED: trusted runner failed: {exc}", file=sys.stderr)
        return 1

    elapsed = perf_counter() - started
    ok = (
        accepted(result, len(identities))
        and len(identities) == EXPECTED_COUNT
        and elapsed <= MAX_SECONDS
    )
    print(
        f"Fast isolated contracts: {result.testsRun}/{EXPECTED_COUNT} "
        f"in {elapsed:.3f}s (budget {MAX_SECONDS:.1f}s), "
        f"skipped={len(result.skipped)}, integrity=VERIFIED",
        flush=True,
    )
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with Path(summary_path).open("a", encoding="utf-8") as summary:
            summary.write(
                f"- Memory DB fast: {result.testsRun}/{EXPECTED_COUNT} tests, "
                f"{elapsed:.3f}s / {MAX_SECONDS:.1f}s, skipped={len(result.skipped)}, "
                f"{'PASS' if ok else 'FAIL'}\n"
            )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
