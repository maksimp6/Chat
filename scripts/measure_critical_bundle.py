#!/usr/bin/env python3
"""Measure the byte size of the web app's critical (pre-first-render) resource set.

The critical set is derived directly from templates/index.html rather than
hardcoded, so this script and the `data-critical-script` contract enforced by
tests/test_frontend_script_contract.py cannot silently drift apart:

- the synchronous boot script (`id="alice-boot"`);
- every script tagged `data-critical-script="..."` (the fetch/dispatcher/core
  chain the boot sequence depends on to reach a usable shell);
- the first stylesheet `<link rel="stylesheet">` in <head> (the styling the
  shell needs for its first paint);
- the HTML document itself.

Methodology, the raw-vs-gzip rule, and the documented limit are described in
docs/frontend/critical-bundle-budget.md. Run this file directly for a human
report, or with --check to enforce the CI budget (see that doc for the
current numbers and why 50 KB is not reachable yet).
"""

from __future__ import annotations

import argparse
import gzip
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_PATH = ROOT / "templates" / "index.html"
STATIC_ROOT = ROOT / "static"

# Target from issue #223. Kept only as an informational comparison: measuring
# against it does not gate CI, since it is not reachable without the broader
# CSS/JS restructuring tracked in #227 (see the doc above).
TARGET_BYTES = 50 * 1024

# CI budget for scripts/measure_critical_bundle.py --check. This is the
# regression gate: it must stay above the measured critical-set size so CI
# passes today, and it must be lowered deliberately (with the doc updated)
# whenever the critical set shrinks. See docs/frontend/critical-bundle-budget.md.
DEFAULT_LIMIT_BYTES = 96 * 1024

CRITICAL_SCRIPT_RE = re.compile(
    r'<script\b(?P<attrs>[^>]*\bsrc="\{\{ static_root \}\}/(?P<name>[^"?]+)\?[^"]*"[^>]*)>'
)
STYLESHEET_RE = re.compile(
    r'<link\b[^>]*\brel="stylesheet"[^>]*\bhref="\{\{ static_root \}\}/(?P<name>[^"?]+)\?[^"]*"[^>]*/?>'
)


def _resolve(relative_name: str) -> Path:
    path = STATIC_ROOT / relative_name
    if not path.is_file():
        raise FileNotFoundError(f"Referenced static resource is missing: {relative_name}")
    return path


def discover_critical_files() -> list[tuple[str, Path]]:
    """Derive the critical file list from templates/index.html's own markers."""
    html = TEMPLATE_PATH.read_text(encoding="utf-8")

    files: list[tuple[str, Path]] = [("templates/index.html", TEMPLATE_PATH)]

    stylesheet = STYLESHEET_RE.search(html)
    if not stylesheet:
        raise RuntimeError('No <link rel="stylesheet"> found in templates/index.html')
    name = stylesheet.group("name")
    files.append((f"static/{name}", _resolve(name)))

    boot_match = re.search(
        r'<script\b[^>]*\bid="alice-boot"[^>]*\bsrc="\{\{ static_root \}\}/([^"?]+)\?', html
    )
    if not boot_match:
        raise RuntimeError('No script with id="alice-boot" found in templates/index.html')
    boot_name = boot_match.group(1)
    files.append((f"static/{boot_name}", _resolve(boot_name)))

    critical_scripts = []
    for match in CRITICAL_SCRIPT_RE.finditer(html):
        if 'data-critical-script="' in match.group("attrs"):
            critical_scripts.append(match.group("name"))
    if not critical_scripts:
        raise RuntimeError(
            'No script with data-critical-script="..." found in templates/index.html'
        )
    for name in critical_scripts:
        files.append((f"static/{name}", _resolve(name)))

    return files


def discover_full_page_files() -> list[tuple[str, Path]]:
    """All local script/stylesheet resources templates/index.html references."""
    html = TEMPLATE_PATH.read_text(encoding="utf-8")
    files: list[tuple[str, Path]] = [("templates/index.html", TEMPLATE_PATH)]

    seen: set[str] = set()
    resource_re = re.compile(r'\{\{ static_root \}\}/([^"?]+)\?v=\{\{ static_version \}\}')
    for match in resource_re.finditer(html):
        name = match.group(1)
        if name in seen:
            continue
        seen.add(name)
        files.append((f"static/{name}", _resolve(name)))

    return files


def measure(files: list[tuple[str, Path]]) -> list[dict]:
    rows = []
    for label, path in files:
        raw = path.read_bytes()
        rows.append(
            {
                "file": label,
                "raw_bytes": len(raw),
                "gzip_bytes": len(gzip.compress(raw, compresslevel=9)),
            }
        )
    return rows


def _print_report(title: str, rows: list[dict]) -> tuple[int, int]:
    total_raw = sum(row["raw_bytes"] for row in rows)
    total_gzip = sum(row["gzip_bytes"] for row in rows)
    print(f"\n{title}")
    print(f"{'file':<45} {'raw':>10} {'gzip':>10}")
    for row in rows:
        print(f"{row['file']:<45} {row['raw_bytes']:>10} {row['gzip_bytes']:>10}")
    print(f"{'TOTAL':<45} {total_raw:>10} {total_gzip:>10}")
    return total_raw, total_gzip


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit 1 if the critical-set raw size exceeds --limit-bytes (CI gate).",
    )
    parser.add_argument(
        "--limit-bytes",
        type=int,
        default=DEFAULT_LIMIT_BYTES,
        help=f"CI budget for the critical set in bytes (default: {DEFAULT_LIMIT_BYTES}).",
    )
    args = parser.parse_args()

    critical_rows = measure(discover_critical_files())
    full_rows = measure(discover_full_page_files())

    critical_raw, critical_gzip = _print_report(
        "Critical set (shell HTML + first stylesheet + boot/core_api/ui_runtime/dispatcher/core):",
        critical_rows,
    )
    full_raw, full_gzip = _print_report(
        "Full page (every local script/stylesheet templates/index.html references):",
        full_rows,
    )

    within_target = "within" if critical_raw <= TARGET_BYTES else "OVER"
    target_delta = abs(critical_raw - TARGET_BYTES)
    print(
        f"\nCritical set: {critical_raw} B raw / {critical_gzip} B gzip "
        f"(target: {TARGET_BYTES} B, {within_target} target by {target_delta} B raw)"
    )
    print(f"Full page:    {full_raw} B raw / {full_gzip} B gzip")
    print(
        "\nRaw bytes are the authoritative number: the app does not configure HTTP "
        "compression (see docs/frontend/critical-bundle-budget.md). Gzip is reported "
        "for reference in case a reverse proxy adds compression later."
    )

    if args.check:
        if critical_raw > args.limit_bytes:
            print(
                f"\nFAIL: critical set is {critical_raw} B raw, over the CI budget of "
                f"{args.limit_bytes} B. Update the code or, if the growth is deliberate, "
                "raise --limit-bytes together with docs/frontend/critical-bundle-budget.md."
            )
            return 1
        print(f"\nOK: critical set is within the CI budget of {args.limit_bytes} B.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
