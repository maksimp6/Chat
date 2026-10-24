from __future__ import annotations

from scripts.check_docs_navigation import validate_navigation


def test_current_section_rejects_noncurrent_catalog_target():
    catalog = {
        "docs/current.md": {"status": "current", "canonical": True},
        "docs/old.md": {"status": "historical", "canonical": False},
    }
    text = """# Docs

## Current

- [Current](current.md)
- [Old](old.md)

## Historical / retired / not planned

"""
    errors = validate_navigation(text, catalog)
    assert errors == [
        "docs/old.md: historical document linked from current navigation section"
    ]


def test_historical_section_accepts_noncurrent_target():
    catalog = {
        "docs/old.md": {"status": "historical", "canonical": False},
        "docs/retired.md": {"status": "retired", "canonical": False},
    }
    text = """# Docs

## Historical / retired / not planned

- [Old](old.md)
- [Retired](retired.md)
"""
    assert validate_navigation(text, catalog) == []


def test_external_and_uncatalogued_links_are_outside_lifecycle_check():
    catalog = {"docs/current.md": {"status": "current", "canonical": True}}
    text = """# Docs

## Current

- [Web](https://example.com)
- [Root](../README.md)
"""
    assert validate_navigation(text, catalog) == []
