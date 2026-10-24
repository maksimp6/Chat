"""Deterministic publication-gate checks for the synthetic Android message fixtures (#1066).

The dataset README requires every added line to be synthetic, to use literal placeholders only,
and to pass CI validation. These tests enforce that gate so later edits cannot publish real-looking
digits, contact details, URLs or non-placeholder identifiers without failing CI.
"""

import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "datasets" / "android_messages" / "synthetic_ru.jsonl"

REQUIRED_FIELDS = {"id", "source", "category", "text", "synthetic", "language"}
ALLOWED_SOURCES = {"sms", "notification"}
PLACEHOLDER = re.compile(r"\[[A-Z_]+\]")
BRACKETED = re.compile(r"\[[^\]]*\]")
CONTACT_OR_URL = re.compile(r"@|https?://|www\.|\+7|\b8\d{10}\b", re.IGNORECASE)


def _rows():
    lines = FIXTURES.read_text(encoding="utf-8").splitlines()
    assert lines, "fixture file must not be empty"
    return [json.loads(line) for line in lines]


def _text_without_placeholders(text):
    return PLACEHOLDER.sub("", text)


def test_fixture_file_is_utf8_jsonl_with_trailing_newline():
    raw = FIXTURES.read_bytes()
    assert raw.endswith(b"\n")
    assert b"\r\n" not in raw
    raw.decode("utf-8")


def test_every_row_has_exact_required_fields_and_is_synthetic_russian():
    for row in _rows():
        assert set(row) == REQUIRED_FIELDS, row.get("id")
        assert row["synthetic"] is True, row["id"]
        assert row["language"] == "ru", row["id"]
        assert row["source"] in ALLOWED_SOURCES, row["id"]


def test_row_ids_are_unique():
    counts = Counter(row["id"] for row in _rows())
    duplicates = sorted(key for key, value in counts.items() if value > 1)
    assert duplicates == []


def test_text_contains_no_digits_or_latin_identifiers_outside_placeholders():
    for row in _rows():
        stripped = _text_without_placeholders(row["text"])
        assert not re.search(r"\d", stripped), row["id"]
        assert not re.search(r"[A-Za-z]", stripped), row["id"]
        assert not any(ord(ch) < 0x20 for ch in row["text"]), row["id"]


def test_bracketed_tokens_are_literal_placeholders_only():
    for row in _rows():
        for token in BRACKETED.findall(row["text"]):
            assert PLACEHOLDER.fullmatch(token), (row["id"], token)


def test_text_contains_no_contact_or_url_patterns():
    for row in _rows():
        assert not CONTACT_OR_URL.search(row["text"]), row["id"]
