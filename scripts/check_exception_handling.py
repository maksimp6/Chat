"""Ratchet for exception handling in project-owned JavaScript, Java and Kotlin.

Issue #1288: blanket and silent handlers hide defects. Python is already ratcheted
by ruff (BLE001, S110, S112, E722). This gate covers the other languages:

- ``try`` blocks per file (``try {``), so new handlers need a reviewed baseline change;
- silent catch blocks per file: a catch whose body is empty or contains only comments.

Counts may only go down. A new file or an increase fails; a decrease also fails until
the baseline is lowered in the same change, so every fix locks itself in.

Run ``python scripts/check_exception_handling.py --write-baseline`` only after a
reviewed change that lowers counts or adds an explicitly justified handler.
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "scripts" / "exception_handling_baseline.json"
SUFFIXES = (".js", ".mjs", ".java", ".kt")
EXCLUDED_ANYWHERE = frozenset({".git", "node_modules"})
# Only top-level directories: static/tests/ and similar project code is scanned.
EXCLUDED_TOP_LEVEL = frozenset({"tests", "build"})
# Vendored third-party library, not project-owned code.
EXCLUDED_FILES = frozenset({"static/eruda.js"})

_TOKEN = re.compile(
    r'"(?:\\.|[^"\\\n])*"'
    r"|'(?:\\.|[^'\\\n])*'"
    r"|`(?:\\.|[^`\\])*`"
    r"|//[^\n]*"
    r"|/\*.*?\*/",
    re.DOTALL,
)
_TRY = re.compile(r"\btry\s*[({]")  # `try (` is Java try-with-resources
_CATCH = re.compile(r"\bcatch\s*(?:\([^)]*\))?\s*\{")
_ARROW_CATCH = re.compile(r"\.catch\s*\(\s*(?:\([^)]*\)|\w+)\s*=>\s*\{")


@dataclass
class Counts:
    try_blocks: int = 0
    silent_catches: int = 0


@dataclass
class Report:
    try_blocks: Counter = field(default_factory=Counter)
    silent_catches: Counter = field(default_factory=Counter)


def _strip_comments(source: str) -> str:
    """Remove comments and string contents so braces inside them are never counted."""

    def replace(match: re.Match[str]) -> str:
        text = match.group(0)
        if text.startswith(("//", "/*")):
            return ""
        return text[0] * 2

    return _TOKEN.sub(replace, source)


def _block_body(source: str, open_brace: int) -> str:
    depth = 0
    for index in range(open_brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[open_brace + 1 : index]
    return ""


def scan_source(source: str) -> Counts:
    code = _strip_comments(source)
    silent = 0
    for pattern in (_CATCH, _ARROW_CATCH):
        for match in pattern.finditer(code):
            if not _block_body(code, match.end() - 1).strip():
                silent += 1
    return Counts(try_blocks=len(_TRY.findall(code)), silent_catches=silent)


def _is_excluded(relative: Path) -> bool:
    parts = relative.parts
    if EXCLUDED_ANYWHERE & set(parts) or parts[0] in EXCLUDED_TOP_LEVEL:
        return True
    return relative.as_posix() in EXCLUDED_FILES


def _project_files(root: Path = ROOT) -> list[Path]:
    files = []
    for directory, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            name
            for name in dirnames
            if not _is_excluded((Path(directory) / name).relative_to(root))
        ]
        for name in filenames:
            path = Path(directory, name)
            if path.suffix in SUFFIXES and not _is_excluded(path.relative_to(root)):
                files.append(path)
    return sorted(files)


def scan_repository(root: Path = ROOT) -> Report:
    report = Report()
    for path in _project_files(root):
        counts = scan_source(path.read_text(encoding="utf-8", errors="strict"))
        relative = path.relative_to(root).as_posix()
        if counts.try_blocks:
            report.try_blocks[relative] = counts.try_blocks
        if counts.silent_catches:
            report.silent_catches[relative] = counts.silent_catches
    return report


def load_baseline(path: Path | None = None) -> dict[str, dict[str, int]]:
    data = json.loads((path or BASELINE).read_text(encoding="utf-8"))
    return {"try_blocks": data["try_blocks"], "silent_catches": data["silent_catches"]}


def compare(report: Report, baseline: dict[str, dict[str, int]]) -> list[str]:
    problems: list[str] = []
    for kind, current in (
        ("try_blocks", report.try_blocks),
        ("silent_catches", report.silent_catches),
    ):
        allowed = baseline[kind]
        for path in sorted(set(current) | set(allowed)):
            now = current.get(path, 0)
            limit = allowed.get(path, 0)
            if now > limit:
                problems.append(f"{kind} grew in {path}: {limit} -> {now}")
            elif now < limit:
                problems.append(f"{kind} shrank in {path}: {limit} -> {now}; lower the baseline")
    return problems


def write_baseline(report: Report, path: Path | None = None) -> None:
    data = {
        "try_blocks": dict(sorted(report.try_blocks.items())),
        "silent_catches": dict(sorted(report.silent_catches.items())),
    }
    (path or BASELINE).write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def main(argv: list[str]) -> int:
    report = scan_repository()
    if "--write-baseline" in argv:
        write_baseline(report)
        return 0
    problems = compare(report, load_baseline())
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
