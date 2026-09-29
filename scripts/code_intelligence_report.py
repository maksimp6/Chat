#!/usr/bin/env python3
"""Build a compact machine-readable code-intelligence report for Alice Pro."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
import subprocess
import sys
from typing import Any


def _run(command: list[str], *, allow_findings: bool = False) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(command, text=True, capture_output=True)
    if completed.returncode and not allow_findings:
        raise SystemExit(
            f"command failed ({completed.returncode}): {' '.join(command)}\n"
            f"{completed.stdout}{completed.stderr}"
        )
    return completed


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _ruff_complexity(root: Path) -> list[dict[str, Any]]:
    completed = _run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            str(root),
            "--select",
            "C90",
            "--output-format",
            "json",
        ],
        allow_findings=True,
    )
    text = completed.stdout.strip()
    return json.loads(text) if text else []


def _vulture_findings(root: Path) -> list[dict[str, Any]]:
    completed = _run(
        [
            sys.executable,
            "-m",
            "vulture",
            str(root),
            "--min-confidence",
            "80",
        ],
        allow_findings=True,
    )
    findings: list[dict[str, Any]] = []
    pattern = re.compile(r"^(.*?):(\\d+):\\s+(.*)$")
    for raw in completed.stdout.splitlines():
        line = raw.strip()
        if not line:
            continue
        match = pattern.match(line)
        if match:
            findings.append(
                {
                    "path": match.group(1),
                    "line": int(match.group(2)),
                    "message": match.group(3),
                }
            )
        else:
            findings.append({"path": "", "line": 0, "message": line})
    return findings


def build_report(root: Path, workdir: Path) -> dict[str, Any]:
    workdir.mkdir(parents=True, exist_ok=True)
    ast_path = workdir / "ai-index.json"
    hot_json = workdir / "hot-file-metrics.json"
    hot_md = workdir / "hot-file-metrics.md"

    _run(
        [
            sys.executable,
            str(root / "scripts" / "build_ai_index.py"),
            "--root",
            str(root),
            "--output",
            str(ast_path),
            "--compact",
        ]
    )
    _run(
        [
            sys.executable,
            str(root / "scripts" / "hot_file_metrics.py"),
            "--repo-root",
            str(root),
            "--commits",
            "200",
            "--top",
            "20",
            "--json-out",
            str(hot_json),
            "--markdown-out",
            str(hot_md),
        ]
    )

    ast_index = _load_json(ast_path)
    hot_files = _load_json(hot_json)
    complexity = _ruff_complexity(root)
    dead_code = _vulture_findings(root)

    return {
        "schema_version": 1,
        "sources": {
            "ast_index": "scripts/build_ai_index.py",
            "hot_files": "scripts/hot_file_metrics.py",
            "complexity": "ruff C90",
            "dead_code": "vulture",
            "runtime_profiler": "pyinstrument",
            "memory_profiler": "tracemalloc",
            "test_timings": "pytest JUnit XML",
        },
        "summary": {
            "python_files": ast_index.get("summary", {}).get("files", 0),
            "symbols": ast_index.get("summary", {}).get("symbols", 0),
            "hot_files": len(hot_files.get("files", [])),
            "complexity_findings": len(complexity),
            "dead_code_candidates": len(dead_code),
        },
        "complexity": complexity,
        "dead_code": dead_code,
        "artifacts": {
            "ai_index": ast_path.name,
            "hot_file_metrics": hot_json.name,
            "hot_file_markdown": hot_md.name,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--output-dir", default="code-intelligence")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    output_dir = Path(args.output_dir)
    if not output_dir.is_absolute():
        output_dir = root / output_dir
    report = build_report(root, output_dir)
    output = output_dir / "report.json"
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report["summary"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
