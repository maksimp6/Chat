"""Fail the required Application tests check when the C17 matrix regresses.

The C17 safety workflow is path-filtered and is not currently a required master
status. This test attaches its fixture contract to the required Python CI lane.
"""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_MODES = ("gcc debug PASS", "gcc release PASS", "clang debug PASS", "clang release PASS")
MIN_EXHAUSTIVE_CASES = 314_721


def test_c17_matrix_is_part_of_required_application_tests() -> None:
    if sys.platform != "linux":
        pytest.skip("Host C17 sanitizer matrix is supported on Linux CI")
    if os.environ.get("CI") and os.environ.get("ALICE_DATABASE_URL"):
        pytest.skip("PostgreSQL lane: C17 is enforced by the required Application tests lane")

    missing = [compiler for compiler in ("gcc", "clang") if shutil.which(compiler) is None]
    if missing:
        if os.environ.get("CI"):
            pytest.fail(f"Required C17 compilers are unavailable: {', '.join(missing)}")
        pytest.skip(f"Missing local C17 compiler: {', '.join(missing)}")

    env = os.environ.copy()
    env.update(
        ASAN_OPTIONS="detect_leaks=1:halt_on_error=1",
        UBSAN_OPTIONS="halt_on_error=1:print_stacktrace=1",
    )
    run = subprocess.run(
        ["bash", "scripts/check_c17.sh"],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
        timeout=90,
    )
    assert run.returncode == 0, f"C17 matrix failed (exit {run.returncode}):\n{run.stdout}"

    lines = run.stdout.splitlines()
    for label in REQUIRED_MODES:
        assert lines.count(label) == 1, f"C17 matrix did not report {label}:\n{run.stdout}"

    counts = re.findall(
        r"^checked_copy: (\d+) exhaustive cases plus negative/boundary tests$",
        run.stdout,
        flags=re.MULTILINE,
    )
    assert len(counts) == len(REQUIRED_MODES), f"Missing C17 case evidence:\n{run.stdout}"
    assert min(int(count) for count in counts) >= MIN_EXHAUSTIVE_CASES, (
        f"C17 exhaustive coverage fell below {MIN_EXHAUSTIVE_CASES}: {counts}"
    )
