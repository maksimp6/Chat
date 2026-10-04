"""OWASP ASVS L2 checklist contract; see docs/security/asvs-l2.md."""

from collections import Counter
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CHECKLIST = ROOT / "docs" / "security" / "asvs-l2.yaml"
STATUSES = {"met", "partial", "gap", "n/a"}

# Gaps may only go down; close one and lower this in the same change.
GAP_BASELINE = 2


def _document():
    return yaml.safe_load(CHECKLIST.read_text(encoding="utf-8"))


def _requirements():
    return _document()["requirements"]


def test_checklist_is_pinned_to_asvs_5_0_0_with_versioned_ids():
    document = _document()
    assert document["version"] == "5.0.0"
    assert document["level"] == 2
    assert document["source"] == "OWASP/ASVS v5.0.0"
    assert "4.0.3 -> 5.0.0" in document["mapping_source"]

    ids = [item["id"] for item in document["requirements"]]
    assert ids
    assert all(item_id.startswith("v5.0.0-") for item_id in ids)


def test_checklist_entries_are_well_formed():
    requirements = _requirements()
    ids = [item["id"] for item in requirements]
    assert len(ids) == len(set(ids)), "duplicate ASVS ids"
    for item in requirements:
        assert item["status"] in STATUSES, item["id"]
        assert item["title"].strip(), item["id"]
        if item["status"] != "met":
            assert item.get("note", "").strip(), f"{item['id']} needs a note explaining the status"


def test_met_requirements_point_to_existing_evidence():
    for item in _requirements():
        if item["status"] != "met":
            continue
        evidence = item.get("evidence", "")
        assert evidence, f"{item['id']} is met but names no evidence"
        path, _, test_name = evidence.partition("::")
        source = ROOT / path
        assert source.is_file(), f"{item['id']} evidence missing: {path}"
        if test_name:
            assert f"def {test_name}(" in source.read_text(encoding="utf-8"), evidence


def test_gaps_only_go_down():
    gaps = Counter(item["status"] for item in _requirements())["gap"]
    assert gaps <= GAP_BASELINE, f"new ASVS gaps ({gaps} > {GAP_BASELINE})"
    assert gaps == GAP_BASELINE, f"lower GAP_BASELINE to {gaps} to lock in the fix"
