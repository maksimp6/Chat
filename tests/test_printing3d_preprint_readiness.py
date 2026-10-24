"""Pre-print preparation cannot approve unknown quality or hardware conditions."""

import json
from pathlib import Path

from printing3d.preprint_readiness import prepare_print

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "printing3d/assets"
MANIFEST = json.loads((ASSETS / "models_manifest.json").read_text())


def passport(name):
    return json.loads((ASSETS / "passports" / (name + ".json")).read_text())


def test_all_models_block_without_physical_evidence():
    for model in MANIFEST["models"]:
        report = prepare_print(MANIFEST, ROOT, passport(model["id"]))
        assert report["status"] == "BLOCKED"
        assert report["checks"]["source_geometry"] == "PASS"
        assert report["checks"]["quality"] == "UNKNOWN"
        assert report["print_start_authorized"] is False


def test_spoofed_operator_approval_cannot_override_unknown_quality():
    p = passport("oem_spindle_v03")
    observed = {
        "operator_approval": True,
        "firmware_open_verified": True,
        "bed_clean": True,
        "bed_clean_evidence": "claimed",
        "physical_fit": True,
        "physical_fit_evidence": "claimed",
    }
    report = prepare_print(MANIFEST, ROOT, p, observed=observed)
    assert report["status"] == "BLOCKED"
    assert report["checks"]["operator_approval"] == "BLOCKED"


def test_mismatched_material_not_ready():
    p = passport("desk_spool_v01")
    report = prepare_print(MANIFEST, ROOT, p, observed={"material_loaded": "PLA"})
    assert report["checks"]["material_loaded"] == "UNKNOWN"
    assert report["status"] == "BLOCKED"
