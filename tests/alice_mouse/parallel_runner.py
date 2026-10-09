"""Isolated Alice Mouse test runner (Issue #650).

Requires the Alice Mouse test modules and compiled C fixture to be installed
in the test environment. No production mouse or Magisk access is performed.
"""
from __future__ import annotations

import concurrent.futures
import subprocess
import sys
import time

GROUPS = (
    ("process", ("test_alice_mouse_process_switch",)),
    ("crash", ("test_alice_mouse_crash_recovery",)),
    ("http", ("test_alice_mouse_http_stage", "test_alice_mouse_e2e_synthetic")),
    ("policy", ("test_alice_mouse_root_bridge", "test_alice_mouse_supervisor",
                "test_alice_mouse_policy", "test_alice_mouse_broker")),
    ("socket", ("test_alice_mouse_boot_recovery", "test_alice_mouse_shared_lock",
                "test_alice_mouse_socket", "test_alice_mouse_bridge")),
)
EXPECTED_TESTS = 52


def run_group(group: tuple[str, tuple[str, ...]]) -> tuple[str, float, int, str]:
    name, modules = group
    started = time.perf_counter()
    result = subprocess.run(
        [sys.executable, "-m", "unittest", "-q", *modules],
        capture_output=True, text=True, timeout=35, check=False,
    )
    return name, time.perf_counter() - started, result.returncode, result.stderr


def main() -> int:
    started = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(GROUPS)) as pool:
        results = list(pool.map(run_group, GROUPS))
    count = 0
    for name, elapsed, code, output in results:
        summaries = [line for line in output.splitlines() if line.startswith("Ran ")]
        if len(summaries) != 1:
            print(f"FAIL {name}: no unambiguous test count\n{output}", file=sys.stderr)
            return 2
        count += int(summaries[0].split()[1])
        print(f"{name}: {elapsed:.3f}s exit={code} {summaries[0]}", flush=True)
        if code != 0:
            print(output, file=sys.stderr)
    wall = time.perf_counter() - started
    print(f"TESTS={count} INNER_WALL={wall:.3f}s", flush=True)
    return 0 if count == EXPECTED_TESTS and all(r[2] == 0 for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
