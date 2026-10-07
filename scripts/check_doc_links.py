#!/usr/bin/env python3
"""Validate repository-local Markdown links without network access."""

from __future__ import annotations

from pathlib import Path
import re
import sys
from urllib.parse import unquote

LINK_RE = re.compile(r"(?<!!)\[[^\]]*\]\(([^)]+)\)")
IGNORED_PREFIXES = ("http://", "https://", "mailto:", "tel:", "#")


def _destination(raw: str) -> str:
    value = raw.strip()
    if value.startswith("<") and ">" in value:
        value = value[1 : value.index(">")]
    else:
        value = value.split(maxsplit=1)[0]
    return unquote(value)


def _resolve(root: Path, source: Path, destination: str) -> tuple[Path | None, str | None]:
    path_part = destination.split("#", 1)[0].split("?", 1)[0]
    if not path_part or path_part.startswith(IGNORED_PREFIXES):
        return None, None

    candidate = root / path_part.lstrip("/") if path_part.startswith("/") else source.parent / path_part
    try:
        resolved = candidate.resolve(strict=False)
        resolved.relative_to(root.resolve())
    except ValueError:
        return None, f"local link escapes repository: {destination}"
    return resolved, None


def validate_markdown_links(root: Path, files: list[Path]) -> list[str]:
    errors: list[str] = []
    for source in files:
        text = source.read_text(encoding="utf-8")
        source_label = source.relative_to(root).as_posix()
        for match in LINK_RE.finditer(text):
            destination = _destination(match.group(1))
            if destination.startswith(IGNORED_PREFIXES):
                continue
            target, error = _resolve(root, source, destination)
            if error:
                errors.append(f"{source_label}: {error}")
            elif target is not None and not target.exists():
                errors.append(f"{source_label}: missing local link target: {destination}")
    return errors


def discover_markdown(root: Path) -> list[Path]:
    files = [root / "README.md"] if (root / "README.md").is_file() else []
    files.extend(sorted((root / "docs").rglob("*.md")))
    return files


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    errors = validate_markdown_links(root, discover_markdown(root))
    if errors:
        for error in errors:
            print(f"docs-link: {error}", file=sys.stderr)
        return 1
    print("Documentation local links valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
