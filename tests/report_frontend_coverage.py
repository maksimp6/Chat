#!/usr/bin/env python3
"""Build a deterministic V8 frontend coverage summary from NODE_V8_COVERAGE files."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any


def _covered_functions(payload: dict[str, Any]) -> tuple[int, int]:
    total = 0
    covered = 0
    for entry in payload.get("result", []):
        for function in entry.get("functions", []):
            total += 1
            ranges = function.get("ranges", [])
            if any(int(item.get("count", 0)) > 0 for item in ranges):
                covered += 1
    return total, covered


def build_summary(directory: Path) -> dict[str, Any]:
    files = sorted(directory.glob("*.json"))
    if not files:
        raise ValueError(f"No V8 coverage files found in {directory}")

    file_summaries = []
    total = 0
    covered = 0
    for path in files:
        payload = json.loads(path.read_text(encoding="utf-8"))
        file_total, file_covered = _covered_functions(payload)
        total += file_total
        covered += file_covered
        file_summaries.append(
            {
                "file": path.name,
                "functions": file_total,
                "covered_functions": file_covered,
            }
        )

    percent = round(covered * 100 / total, 2) if total else 100.0
    return {
        "schema_version": 1,
        "metric": "v8_function_coverage",
        "files": file_summaries,
        "functions": total,
        "covered_functions": covered,
        "percent": percent,
    }


def main() -> int:
    if len(sys.argv) != 3:
        print(f"usage: {sys.argv[0]} COVERAGE_DIR OUTPUT_JSON", file=sys.stderr)
        return 2

    try:
        summary = build_summary(Path(sys.argv[1]))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"frontend coverage: {exc}", file=sys.stderr)
        return 1

    Path(sys.argv[2]).write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        "Frontend V8 function coverage: "
        f"{summary['covered_functions']}/{summary['functions']} "
        f"({summary['percent']:.2f}%)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
