"""Ratchet for docs/development/reliability.md.

Counts may only go down: a new violation fails, and a fix must lower its
baseline in the same change.
"""

import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

RATCHET_BASELINE = {
    "C901": 72,
    "PLR0915": 19,
    "PLR0912": 46,
    "PLR0911": 20,
    "BLE001": 104,
    "E722": 0,
    "S110": 8,
    "S112": 1,
    "PLW0603": 4,
    "B006": 0,
    "RUF012": 3,
    "B904": 1,
    "S307": 0,
    "S102": 1,
}

EXCLUDED_PREFIXES = ("tests/", "node_modules/")


def _violation_counts():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            ".",
            "--select",
            ",".join(RATCHET_BASELINE),
            "--output-format",
            "json",
            "--exit-zero",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
        timeout=300,
    )
    counts = Counter()
    for violation in json.loads(result.stdout):
        relative = Path(violation["filename"]).resolve().relative_to(ROOT).as_posix()
        if not relative.startswith(EXCLUDED_PREFIXES):
            counts[violation["code"]] += 1
    return counts


def test_reliability_violations_only_go_down():
    counts = _violation_counts()
    grown = {
        rule: (counts[rule], limit)
        for rule, limit in RATCHET_BASELINE.items()
        if counts[rule] > limit
    }
    assert not grown, (
        "new reliability violations (rule: (now, allowed)); fix them, see "
        f"docs/development/reliability.md: {grown}"
    )
    shrunk = {
        rule: (counts[rule], limit)
        for rule, limit in RATCHET_BASELINE.items()
        if counts[rule] < limit
    }
    assert not shrunk, f"lower RATCHET_BASELINE to lock in the fixes: {shrunk}"
