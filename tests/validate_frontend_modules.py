#!/usr/bin/env python3
"""Strict static policy validator for Alice Pro frontend JavaScript."""

from __future__ import annotations

import math
import re
import subprocess
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"

MAX_JS_BYTES = 512 * 1024
MAX_LINE_BYTES = 8 * 1024
MAX_REPEAT_RUN = 12
MAX_ARRAY_LITERAL_ITEMS = 4096
MAX_ARRAY_CONSTRUCTOR_ITEMS = 4096

FUNCTION_RE = re.compile(r"\b(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(")
BAD_FUNCTION_NAME = re.compile(r"^(?:_|[$]|[A-Z])")
OBFUSCATION_PATTERNS = (
    (re.compile(r"\beval\s*\("), "eval()"),
    (re.compile(r"\bnew\s+Function\s*\("), "dynamic Function constructor"),
    (re.compile(r"\bFunction\s*\("), "Function constructor"),
    (re.compile(r"\b(?:atob|btoa)\s*\("), "base64 runtime codec"),
    (re.compile(r"String\.fromCharCode\s*\("), "character-code construction"),
    (re.compile(r"\\x[0-9a-fA-F]{2}(?:\\x[0-9a-fA-F]{2}){3,}"), "long hex escape sequence"),
)
TEXT_FORBIDDEN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
BASE64_RE = re.compile(r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{4096,}={0,2}(?![A-Za-z0-9+/])")
URL_DATA_RE = re.compile(r"data:[^,]{0,100},([A-Za-z0-9+/=]{4096,})")
BINARY_RE = re.compile(rb"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
INFINITE_LOOP_PATTERNS = (
    (re.compile(r"\bwhile\s*\(\s*true\s*\)"), "while(true)"),
    (re.compile(r"\bfor\s*\(\s*;\s*;\s*\)"), "for(;;)"),
)
UNBOUNDED_LOOP_RE = re.compile(r"\bwhile\s*\(\s*[^\n;{}()]+\s*\)\s*\{")
FETCH_IN_LOOP_RE = re.compile(
    r"\b(?:while|for)\b[\s\S]{0,400}\b(?:fetch|XMLHttpRequest)\s*\("
)
ARRAY_LITERAL_RE = re.compile(r"\[([^\[\]]*)\]", re.DOTALL)
ARRAY_CONSTRUCTOR_RE = re.compile(r"\bnew\s+Array\s*\(\s*(\d{4,})\s*\)")
TIMER_PATTERNS = (
    (re.compile(r"\bsetTimeout\s*\("), "setTimeout"),
    (re.compile(r"\bsetInterval\s*\("), "setInterval"),
    (re.compile(r"\bclearTimeout\s*\("), "clearTimeout"),
    (re.compile(r"\bclearInterval\s*\("), "clearInterval"),
    (re.compile(r"\b(?:delay|sleep)\s*\("), "delay/sleep"),
)
REPEATED_LOOKUP_RE = re.compile(
    r"\b(?:fetch|localStorage\.getItem|sessionStorage\.getItem)\s*\([^\n]*\)"
    r"[\s\S]{0,250}\b(?:fetch|localStorage\.getItem|sessionStorage\.getItem)\s*\("
)


def entropy(value: str) -> float:
    counts = Counter(value)
    n = len(value)
    return -sum((count / n) * math.log2(count / n) for count in counts.values())


def is_dispatcher(path: Path) -> bool:
    return path.name == "dispatcher.js"


def is_vendor(path: Path) -> bool:
    return path.name in {"eruda.js"} or "vendor" in path.parts


def is_policy_exempt(path: Path) -> bool:
    # Service workers have a mandatory fetch event handler and are infrastructure,
    # not application modules. They still undergo JavaScript syntax validation.
    return path.name == "sw.js"


def _timer_errors(text: str, rel: Path) -> list[str]:
    errors = []
    for pattern, label in TIMER_PATTERNS:
        if pattern.search(text):
            errors.append(f"{rel}: timer safety violation: {label} is forbidden; use dispatcher/event lifecycle")
    return errors


def _loop_errors(text: str, rel: Path) -> list[str]:
    errors = []
    for pattern, label in INFINITE_LOOP_PATTERNS:
        if pattern.search(text):
            errors.append(f"{rel}: loop safety violation: unbounded {label}")
    if UNBOUNDED_LOOP_RE.search(text):
        errors.append(f"{rel}: loop safety violation: while-loop has no statically visible bound")
    if FETCH_IN_LOOP_RE.search(text):
        errors.append(
            f"{rel}: loop safety violation: network request inside loop requires explicit bounded/cached design"
        )
    return errors


def _array_errors(text: str, rel: Path) -> list[str]:
    errors = []
    for match in ARRAY_LITERAL_RE.finditer(text):
        body = match.group(1).strip()
        if not body:
            continue
        items = body.count(",") + 1
        if items > MAX_ARRAY_LITERAL_ITEMS:
            errors.append(f"{rel}: array safety violation: literal contains {items} items")
            break
    for match in ARRAY_CONSTRUCTOR_RE.finditer(text):
        size = int(match.group(1))
        if size > MAX_ARRAY_CONSTRUCTOR_ITEMS:
            errors.append(f"{rel}: array safety violation: constructor requests {size} items")
    return errors


def _cache_errors(text: str, rel: Path) -> list[str]:
    errors = []
    if REPEATED_LOOKUP_RE.search(text) and not re.search(
        r"\b(?:cache|memo|memoize|Map|WeakMap)\b", text
    ):
        errors.append(
            f"{rel}: cache safety violation: repeated lookup without visible cache/memoization"
        )
    return errors


def validate_file(path: Path) -> list[str]:
    errors: list[str] = []
    raw = path.read_bytes()
    rel = path.relative_to(ROOT)

    if len(raw) > MAX_JS_BYTES and not is_vendor(path):
        errors.append(f"{rel}: file size {len(raw)} bytes exceeds {MAX_JS_BYTES}")
    if BINARY_RE.search(raw):
        errors.append(f"{rel}: binary/control-byte payload detected")

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        return [f"{rel}: invalid UTF-8: {exc}"]

    for number, line in enumerate(text.splitlines(), 1):
        if len(line.encode("utf-8")) > MAX_LINE_BYTES:
            errors.append(f"{rel}:{number}: line exceeds {MAX_LINE_BYTES} bytes")

    if not is_dispatcher(path) and re.search(r"\bfetch\s*\(", text):
        errors.append(f"{rel}: dispatcher safety violation: direct transport access is forbidden")

    if TEXT_FORBIDDEN.search(text):
        errors.append(f"{rel}: unusual text classification: forbidden control character")

    for pattern, label in OBFUSCATION_PATTERNS:
        if pattern.search(text):
            errors.append(f"{rel}: suspicious/obfuscated construct: {label}")

    if BASE64_RE.search(text) or URL_DATA_RE.search(text):
        errors.append(f"{rel}: large embedded base64/data payload is forbidden")

    for match in FUNCTION_RE.finditer(text):
        name = match.group(1)
        if BAD_FUNCTION_NAME.match(name):
            errors.append(f"{rel}: function name '{name}' violates naming policy")

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    run = 1
    for previous, current in zip(lines, lines[1:]):
        if current == previous:
            run += 1
            if run >= MAX_REPEAT_RUN:
                errors.append(f"{rel}: suspicious repeated source text (consecutive run={run})")
                break
        else:
            run = 1

    if len(text) >= 4096:
        printable = sum(ch.isprintable() or ch in "\n\r\t" for ch in text)
        if printable / len(text) < 0.985:
            errors.append(f"{rel}: unusual text classification: excessive non-printable content")
        if entropy(text) > 5.95 and len(text) > 32 * 1024 and not is_vendor(path):
            errors.append(f"{rel}: unusual text classification: high source entropy")

    errors.extend(_timer_errors(text, rel))
    errors.extend(_loop_errors(text, rel))
    errors.extend(_array_errors(text, rel))
    errors.extend(_cache_errors(text, rel))
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
        return [
            f"{path.relative_to(ROOT)}: JavaScript syntax error: "
            f"{detail[-1] if detail else 'unknown'}"
        ]
    return []


def main() -> int:
    files = sorted(STATIC.rglob("*.js"))
    errors: list[str] = []

    for path in files:
        if not is_vendor(path) and not is_policy_exempt(path):
            errors.extend(validate_file(path))
        errors.extend(validate_syntax(path))

    if errors:
        print("Frontend policy validation failed:")
        for error in errors:
            print(f"ERROR {error}")
        print(f"\n{len(errors)} error(s). No warnings or notes are emitted by this validator.")
        return 1

    print(
        f"Frontend policy validation passed: {len(files)} JavaScript file(s), "
        "0 errors, 0 warnings, 0 notes."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
