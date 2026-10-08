#!/usr/bin/env python3
"""Validate machine-verifiable repository references in operational docs."""

from __future__ import annotations

from pathlib import Path
import re
import sys

CODE_RE = re.compile(r"`([^`\n]+)`")
REPO_PREFIXES = ("scripts/", ".github/workflows/")
PYTHON_MODULE_RE = re.compile(r"^python(?:3)?\s+-m\s+([A-Za-z_][A-Za-z0-9_.]*)\b")


def _module_exists(root: Path, module: str) -> bool:
    relative = Path(*module.split("."))
    return (root / f"{relative}.py").is_file() or (root / relative / "__main__.py").is_file()


def validate_runbook_references(root: Path, files: list[Path]) -> list[str]:
    errors: list[str] = []
    for source in files:
        label = source.relative_to(root).as_posix()
        text = source.read_text(encoding="utf-8")
        for raw in CODE_RE.findall(text):
            value = raw.strip()
            if value.startswith(REPO_PREFIXES):
                target = value.split(maxsplit=1)[0].rstrip(".,:;")
                if not (root / target).is_file():
                    errors.append(f"{label}: missing repository path: {target}")
                continue
            match = PYTHON_MODULE_RE.match(value)
            if match and not _module_exists(root, match.group(1)):
                errors.append(f"{label}: missing Python module: {match.group(1)}")
    return errors


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    files = sorted((root / "docs").rglob("*.md"))
    errors = validate_runbook_references(root, files)
    if errors:
        for error in errors:
            print(f"runbook-ref: {error}", file=sys.stderr)
        return 1
    print("Operational repository references valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
