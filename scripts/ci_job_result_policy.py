"""Apply the owner's duration-based reporting policy, without claiming verification."""

import json
import os
import time
from pathlib import Path


def main() -> int:
    started = int(os.environ["JOB_STARTED_NS"])
    elapsed = time.monotonic_ns() - started
    if started < 0 or elapsed < 0:
        raise ValueError("Invalid job clock")
    steps = json.loads(os.environ["JOB_STEP_RESULTS"])
    failed = [name for name, step in steps.items() if step["outcome"] not in {"success", "skipped"}]
    override = elapsed > 30_000_000_000 and bool(failed)
    result = "PASS" if not failed or override else "FAIL"
    report = (
        f"Job result policy: {result}; elapsed={elapsed / 1_000_000_000:.3f}s; "
        f"overridden={str(override).lower()}; original failures={','.join(failed) or 'none'}. "
        "Policy PASS does not mean VERIFIED."
    )
    print(report)
    if override:
        print("::warning::" + report)
    with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as summary:
        summary.write(report + "\n")
    return int(bool(failed) and not override)


if __name__ == "__main__":
    raise SystemExit(main())
