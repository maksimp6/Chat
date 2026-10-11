"""Sum timed fast imports and tests; fail closed if either report is untrusted.

Only in-process module import and test execution count towards the 2s limit.
Interpreter startup and shell/workflow setup are separately observable.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import re

LIMIT_SECONDS = 2.0
TRUSTED_PATTERN = re.compile(
    r"^Fast isolated contracts: 15/15 in ([0-9]+\.[0-9]{3})s "
    r"\(budget 2\.0s\), skipped=0, integrity=VERIFIED$",
    re.MULTILINE,
)
LEGACY_PATTERN = re.compile(
    r"^Fast isolated contracts: 15 tests in ([0-9]+\.[0-9]{3})s "
    r"\(budget 2\.0s\)$",
    re.MULTILINE,
)


def combined_seconds(trusted_output: str, legacy_output: str) -> float:
    """Require exactly one successful, pinned-format summary per executor."""
    trusted = TRUSTED_PATTERN.findall(trusted_output)
    legacy = LEGACY_PATTERN.findall(legacy_output)
    if len(trusted) != 1 or len(legacy) != 1:
        raise ValueError("missing, duplicated or invalid fast-contract summary")
    return float(trusted[0]) + float(legacy[0])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trusted-log", type=Path, required=True)
    parser.add_argument("--legacy-log", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        total = combined_seconds(
            args.trusted_log.read_text(encoding="utf-8"),
            args.legacy_log.read_text(encoding="utf-8"),
        )
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"BLOCKED: {exc}")
        return 1
    print(f"Combined fast imports and tests: {total:.3f}s / {LIMIT_SECONDS:.3f}s")
    if total > LIMIT_SECONDS:
        print("BLOCKED: aggregate two-second fast-contract budget exceeded")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
