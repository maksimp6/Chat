#!/usr/bin/env python3
"""Run a Python module under tracemalloc and emit allocation hotspots as JSON."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import runpy
import sys
import tracemalloc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="profiling/memory.json")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--frames", type=int, default=25)
    parser.add_argument("--module", required=True)
    parser.add_argument("args", nargs=argparse.REMAINDER)
    options = parser.parse_args()

    output = Path(options.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    argv = [options.module, *options.args]
    old_argv = sys.argv[:]
    exit_code = 0

    tracemalloc.start(max(1, options.frames))
    try:
        sys.argv = argv
        try:
            runpy.run_module(options.module, run_name="__main__", alter_sys=True)
        except SystemExit as exc:
            if isinstance(exc.code, int):
                exit_code = exc.code
            elif exc.code:
                exit_code = 1
    finally:
        snapshot = tracemalloc.take_snapshot()
        tracemalloc.stop()
        sys.argv = old_argv

    rows = []
    for stat in snapshot.statistics("traceback")[: max(1, options.limit)]:
        frame = stat.traceback[0]
        rows.append(
            {
                "path": frame.filename,
                "line": frame.lineno,
                "size_bytes": stat.size,
                "allocation_count": stat.count,
            }
        )

    output.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "module": options.module,
                "args": options.args,
                "top_allocations": rows,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(output)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
