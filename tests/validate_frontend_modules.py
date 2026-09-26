#!/usr/bin/env python3
"""Strict static policy validator for Alice Pro frontend JavaScript.

This is intentionally dependency-free. It validates source before the browser
runtime sees it: syntax, naming, suspicious/obfuscated payloads, embedded
binary/base64 data, repetition, file size, and unusual text.
"""

from __future__ import annotations

import base64
import math
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"

MAX_JS_BYTES = 512 * 1024
MAX_LINE_BYTES = 8 * 1024
MAX_BASE64_BYTES = 4096
MAX_REPEAT_RUN = 12
MAX_REPEAT_RATIO = 0.35

FUNCTION_RE = re.compile(
    r"\b(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\("
)
METHOD_RE = re.compile(
    r"^\s*(?:async\s+)?([A-Za-z_$][\w$]*)\s*\([^)]*\)\s*\{",
    re.MULTILINE,
)
BAD_FUNCTION_NAME = re.compile(r"^(?:_|[$]|[A-Z]|[a-z]{1,2}$)")
OBFUSCATION_PATTERNS = (
    (re.compile(r"\beval\s*\("), "eval()"),
    (re.compile(r"\bnew\s+Function\s*\("), "dynamic Function constructor"),
    (re.compile(r"\bFunction\s*\("), "Function constructor"),
    (re.compile(r"\b(?:atob|btoa)\s*\("), "base64 runtime codec"),
    (re.compile(r"String\.fromCharCode\s*\("), "character-code construction"),
    (re.compile(r"\\x[0-9a-fA-F]{2}(?:\\x[0-9a-fA-F]{2}){3,}"), "long hex escape sequence"),
)
TEXT_FORBIDDEN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
TODO_RE = re.compile(r"\b(?:TODO|FIXME|XXX|HACK|NOTE)\b", re.IGNORECASE)
BASE64_RE = re.compile(r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{4096,}={0,2}(?![A-Za-z0-9+/])")
URL_DATA_RE = re.compile(r"data:[^,]{0,100},([A-Za-z0-9+/=]{4096,})")
BINARY_RE = re.compile(rb"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def entropy(value: str) -> float:
    counts = Counter(value)
    n = len(value)
    return -sum((count / n) * math.log2(count / n) for count in counts.values())


def is_vendor(path: Path) -> bool:
    return path.name in {"eruda.js"} or "vendor" in path.parts


def validate_file(path: Path) -> list[str]:
    errors: list[str] = []
    raw = path.read_bytes()
    rel = path.relative_to(ROOT)

    if len(raw) > MAX_JS_BYTES and not is_vendor(path):
        errors.append(f"{rel}: file size {len(raw)} bytes exceeds {MAX_JS_BYTES}")

    if b"\x00" in raw or BINARY_RE.search(raw):
        errors.append(f"{rel}: binary/control-byte payload detected")

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        errors.append(f"{rel}: invalid UTF-8: {exc}")
        return errors

    for number, line in enumerate(text.splitlines(), 1):
        if len(line.encode("utf-8")) > MAX_LINE_BYTES:
            errors.append(f"{rel}:{number}: line exceeds {MAX_LINE_BYTES} bytes")

    if TODO_RE.search(text):
        errors.append(f"{rel}: TODO/FIXME/XXX/HACK/NOTE marker is forbidden")

    for pattern, label in OBFUSCATION_PATTERNS:
        if pattern.search(text):
            errors.append(f"{rel}: suspicious/obfuscated construct: {label}")

    for match in BASE64_RE.finditer(text):
        if len(match.group(0)) > MAX_BASE64_BYTES:
            errors.append(f"{rel}: large embedded base64 payload ({len(match.group(0))} chars)")
            break
    if URL_DATA_RE.search(text):
        errors.append(f"{rel}: embedded data: base64 payload is forbidden")

    for match in FUNCTION_RE.finditer(text):
        name = match.group(1)
        if BAD_FUNCTION_NAME.match(name):
            errors.append(f"{rel}: function name '{name}' violates naming policy")
    for match in METHOD_RE.finditer(text):
        name = match.group(1)
        if BAD_FUNCTION_NAME.match(name) and name not in {"if", "for", "while", "switch", "catch"}:
            errors.append(f"{rel}: method name '{name}' violates naming policy")

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if lines:
        counts = Counter(lines)
        repeated = sum(count - 1 for count in counts.values() if count > 1)
        ratio = repeated / len(lines)
        longest = max(counts.values())
        if longest >= MAX_REPEAT_RUN or ratio > MAX_REPEAT_RATIO:
            errors.append(
                f"{rel}: suspicious repeated source text "
                f"(ratio={ratio:.2f}, max_repeat={longest})"
            )

    # Unusual text: reject control-heavy or extremely high-entropy source
    # outside comments/strings that are clearly normal source text.
    if len(text) >= 4096:
        printable = sum(ch.isprintable() or ch in "\n\r\t" for ch in text)
        if printable / len(text) < 0.985:
            errors.append(f"{rel}: unusual text classification: excessive non-printable content")
        if entropy(text) > 5.95 and len(text) > 32 * 1024 and not is_vendor(path):
            errors.append(f"{rel}: unusual text classification: high source entropy")

    return errors


def validate_syntax(path: Path) -> list[str]:
    result = subprocess.run(
        ["node", "--check", str(path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout).strip().splitlines()
        return [f"{path.relative_to(ROOT)}: JavaScript syntax error: {detail[-1] if detail else 'unknown'}"]
    return []


def main() -> int:
    files = sorted(STATIC.rglob("*.js"))
    errors: list[str] = []
    for path in files:
        errors.extend(validate_file(path))
        if not is_vendor(path):
            errors.extend(validate_syntax(path))

    if errors:
        print("Frontend policy validation failed:")
        for error in errors:
            print(f"ERROR {error}")
        print(f"\n{len(errors)} error(s). No warnings or notes are emitted by this validator.")
        return 1

    print(f"Frontend policy validation passed: {len(files)} JavaScript file(s), 0 errors, 0 warnings, 0 notes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
