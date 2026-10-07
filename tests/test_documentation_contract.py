from __future__ import annotations

import json
from pathlib import Path

from scripts.ci_platform_changes import classify
from scripts.check_docs import ALLOWED_KINDS, ALLOWED_STATUSES, validate_catalog

ROOT = Path(__file__).resolve().parents[1]


def test_documentation_catalog_covers_every_markdown_file():
    catalog = json.loads((ROOT / "docs" / "catalog.json").read_text(encoding="utf-8"))
    errors = validate_catalog(ROOT, catalog)
    assert errors == []


def test_documentation_catalog_rejects_invalid_metadata(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "one.md").write_text("# One\n", encoding="utf-8")
    catalog = {
        "documents": [
            {
                "path": "docs/one.md",
                "kind": "invalid-kind",
                "status": "current",
                "owner": "docs-engineer",
                "source_of_truth": "code",
            }
        ]
    }
    errors = validate_catalog(tmp_path, catalog)
    assert any("kind" in error for error in errors)


def test_documentation_contract_enums_are_stable():
    assert ALLOWED_KINDS == {
        "tutorial",
        "how-to",
        "runbook",
        "reference",
        "explanation",
        "decision",
        "historical",
        "index",
    }
    assert ALLOWED_STATUSES == {
        "current",
        "experimental",
        "planned",
        "historical",
        "retired",
    }


def test_docs_only_changes_do_not_select_heavy_platform_suites():
    plan = classify(["docs/architecture/overview.md", "README.md"])
    assert not any(plan.values())
