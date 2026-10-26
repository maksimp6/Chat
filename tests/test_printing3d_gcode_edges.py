"""Fail-closed edge paths for G-code audit, mesh preflight, quality and readiness."""

import copy
import hashlib
import json
import struct
import zipfile
from pathlib import Path

import pytest

from printing3d import gcode_audit, model_preflight, preprint_readiness, quality_capability

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "printing3d/assets"
MANIFEST = json.loads((ASSETS / "models_manifest.json").read_text())


def passport(name="desk_spool_v01"):
    return json.loads((ASSETS / "passports" / f"{name}.json").read_text())


def gcode_body(extra="", moves=12):
    lines = ["; HEADER_BLOCK_START", "M190 S60", "M109 S220"]
    lines += [f"G1 X{10 + i} Y{10 + i} Z0.2 E0.5" for i in range(moves)]
    lines += [extra, "; EXECUTABLE_BLOCK_END"]
    return "\n".join(lines) + "\n"


def sliced_job(tmp_path, p, *, gcode=None, md5=None, settings=None, plate=None, name=None):
    body = (gcode if gcode is not None else gcode_body()).encode()
    machine = p["machine"]
    files = {
        "Metadata/plate_1.gcode": body,
        "Metadata/plate_1.gcode.md5": (md5 or hashlib.md5(body).hexdigest()).encode(),
        "Metadata/project_settings.config": json.dumps(
            settings
            or {
                "printer_model": machine["model"],
                "printer_variant": machine["nozzle_mm"],
                "filament_type": [p["material"]],
            }
        ).encode(),
        "Metadata/plate_1.json": json.dumps(plate or {"bbox_all": [10, 10, 60, 60]}).encode(),
    }
    path = tmp_path / (name or "job.gcode.3mf")
    with zipfile.ZipFile(path, "w") as archive:
        for member, data in files.items():
            archive.writestr(member, data)
    return path


def test_valid_synthetic_job_passes_static_audit_but_not_fit(tmp_path):
    p = passport()
    result = gcode_audit.audit_passport(p, sliced_job(tmp_path, p))
    assert result["status"] == "GCODE_VERIFIED_FIT_BLOCKED"
    assert result["motion_commands"] == 12
    assert result["physical_fit_verified"] is False

    verified = copy.deepcopy(p)
    verified.update(fit_status="verified", fit_evidence="caliper log")
    result = gcode_audit.audit_passport(verified, sliced_job(tmp_path, p))
    assert result["status"] == "GCODE_STATIC_VERIFIED"
    assert result["model_id"] == p["id"]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"md5": "0" * 32}, "MD5 mismatch"),
        ({"settings": {"printer_model": "X1"}}, "printer profile mismatch"),
        ({"plate": {"bbox_all": [1, 2]}}, "missing print area"),
        ({"plate": {"bbox_all": [10, 10, 500, 60]}}, "print area outside bed"),
        ({"gcode": "G1 X1\n"}, "incomplete Bambu G-code"),
        ({"gcode": gcode_body("M997")}, "dangerous firmware command"),
        ({"gcode": gcode_body(moves=3)}, "missing toolpaths"),
        ({"gcode": gcode_body("G1 X900 Y10")}, "motion outside bed"),
        ({"gcode": gcode_body("G1 Z900")}, "Z outside printer height"),
    ],
)
def test_job_defects_are_rejected(tmp_path, kwargs, message):
    with pytest.raises(ValueError, match=message):
        gcode_audit.audit_job(sliced_job(tmp_path, passport(), **kwargs), passport())


def test_material_and_archive_members_are_enforced(tmp_path):
    p = passport()
    settings = {
        "printer_model": p["machine"]["model"],
        "printer_variant": p["machine"]["nozzle_mm"],
        "filament_type": ["PLA" if p["material"] != "PLA" else "PETG"],
    }
    with pytest.raises(ValueError, match="material mismatch"):
        gcode_audit.audit_job(sliced_job(tmp_path, p, settings=settings), p)
    empty = tmp_path / "empty.gcode.3mf"
    with zipfile.ZipFile(empty, "w") as archive:
        archive.writestr("Metadata/plate_1.gcode", "")
    with pytest.raises(ValueError, match="invalid sliced archive"):
        gcode_audit.audit_job(empty, p)


def test_non_finite_motion_is_rejected():
    with pytest.raises(ValueError, match="non-finite"):
        gcode_audit._verify_motion_bounds("G1 X" + "9" * 400, [180, 180, 180])


def test_invalid_passport_and_device_evidence_are_rejected():
    with pytest.raises(ValueError, match="invalid model passport"):
        gcode_audit.audit_passport({"id": "x", "schema_version": 2})
    p = passport()
    observed = {"host": p["target_printer"]["host"], "model": p["target_printer"]["model"]}
    with pytest.raises(ValueError, match="device authentication"):
        gcode_audit.verify_target_printer(p, observed)
    observed.update(
        authenticated_mqtt=True,
        ftps_certificate_pin_verified=True,
        serial_match_verified=True,
        firmware_version="01.08.01.00",
        firmware_compatibility_verified=True,
    )
    with pytest.raises(ValueError, match="invalid job hash"):
        gcode_audit.verify_target_printer(p, observed, "not-a-hash")


def stl_bytes(faces):
    data = bytearray(b"t".ljust(80, b" ") + struct.pack("<I", len(faces)))
    for face in faces:
        data += struct.pack("<12fH", 0, 0, 0, *[c for v in face for c in v], 0)
    return bytes(data)


TETRA = [
    ((0, 0, 0), (1, 0, 0), (0, 1, 0)),
    ((0, 0, 0), (0, 1, 0), (0, 0, 1)),
    ((0, 0, 0), (0, 0, 1), (1, 0, 0)),
    ((1, 0, 0), (0, 0, 1), (0, 1, 0)),
]


def stl_record(tmp_path, faces, *, triangles=None, bounds=(1, 1, 1), raw=None):
    data = raw if raw is not None else stl_bytes(faces)
    path = tmp_path / "part.stl"
    path.write_bytes(data)
    record = {
        "sha256": hashlib.sha256(data).hexdigest(),
        "geometry": {
            "triangles": len(faces) if triangles is None else triangles,
            "bounds_mm": list(bounds),
        },
    }
    return path, record


def test_closed_tetrahedron_passes_mesh_preflight(tmp_path):
    path, record = stl_record(tmp_path, TETRA)
    assert model_preflight.validate_stl(path, record)["topology"] == "closed_edge_manifold"


@pytest.mark.parametrize(
    ("faces", "kwargs", "message"),
    [
        (TETRA, {"raw": b"short"}, "truncated"),
        (TETRA, {"triangles": 5}, "face count"),
        ([((0, 0, 0), (0, 0, 0), (1, 1, 1))] + TETRA[1:], {}, "degenerate STL face"),
        (TETRA[:3], {}, "open or nonmanifold"),
        (TETRA, {"bounds": (2, 2, 2)}, "mismatch passport"),
        ([((0, 0, 0), (float("nan"), 0, 0), (0, 1, 0))] + TETRA[1:], {}, "nonfinite"),
    ],
)
def test_mesh_defects_are_rejected(tmp_path, faces, kwargs, message):
    path, record = stl_record(tmp_path, faces, **kwargs)
    with pytest.raises(ValueError, match=message):
        model_preflight.validate_stl(path, record)


def test_mesh_hash_and_bed_limits_are_enforced(tmp_path):
    path, record = stl_record(tmp_path, TETRA)
    with pytest.raises(ValueError, match="outside target printer"):
        model_preflight.validate_stl(path, record, bed=(0, 0, 0))
    record["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="hash mismatch"):
        model_preflight.validate_stl(path, record)


def test_inventory_without_stl_and_upload_evidence_gates(tmp_path):
    manifest = {"models": [{"id": "m", "members_manifest": [{"path": "a.step"}]}]}
    with pytest.raises(ValueError, match="no validated STL"):
        model_preflight.verify_inventory_sources(manifest, tmp_path)
    p = passport()
    good = [{"topology": "closed_edge_manifold"}]
    with pytest.raises(ValueError, match="independent audit"):
        model_preflight.require_upload_preflight(p, good, {"status": "SOURCE_ONLY"})
    with pytest.raises(ValueError, match="source geometry"):
        model_preflight.require_upload_preflight(
            p, [{"topology": "open"}], {"status": "GCODE_STATIC_VERIFIED"}
        )
    accepted = copy.deepcopy(p)
    accepted["target_printer"]["firmware_compatibility_verified"] = True
    result = model_preflight.require_upload_preflight(
        accepted, good, {"status": "GCODE_STATIC_VERIFIED"}
    )
    assert result["status"] == "UPLOAD_PREFLIGHT_PASSED"


def approved(p):
    p = copy.deepcopy(p)
    p.update(quality_requirements_approved=True, process_profile_id="PETG_0.2")
    return p


def evidence(p, value, **overrides):
    record = {
        "evidence_id": "coupon-1",
        "material": p["material"],
        "printer_id": p["target_printer"]["device_id"],
        "profile_id": p["process_profile_id"],
        "value": value,
    }
    record.update(overrides)
    return record


def test_quality_threshold_and_evidence_rules():
    p = approved(passport())
    broken = copy.deepcopy(p)
    broken["quality_requirements"] = [{"metric": "x"}]
    with pytest.raises(ValueError, match="missing numeric threshold"):
        quality_capability.assess_quality(broken, {})
    broken["quality_requirements"] = [{"metric": "x", "max_value": float("inf")}]
    with pytest.raises(ValueError, match="invalid requirement threshold"):
        quality_capability.assess_quality(broken, {})

    metric = p["quality_requirements"][0]["metric"]
    reasons = {
        "evidence for different material or printer": evidence(p, 0.1, material="ABS"),
        "process profile not calibrated": evidence(p, 0.1, profile_id="other"),
        "no numeric measurement": evidence(p, "n/a"),
    }
    for reason, record in reasons.items():
        result = quality_capability.assess_quality(p, {}, {metric: record})
        assert result["checks"][0] == {"metric": metric, "status": "UNKNOWN", "reason": reason}


def test_quality_passes_only_when_every_metric_is_measured():
    p = approved(passport())
    measured = {
        r["metric"]: evidence(p, r.get("max_value", r.get("min_value")))
        for r in p["quality_requirements"]
    }
    assert quality_capability.assess_quality(p, {}, measured)["status"] == "PASS"


def test_readiness_rejects_unknown_model_and_records_evidence(tmp_path):
    p = passport("oem_spindle_v03")
    unknown = copy.deepcopy(p)
    unknown["id"] = "not-in-inventory"
    with pytest.raises(ValueError, match="unknown model"):
        preprint_readiness.prepare_print(MANIFEST, ROOT, unknown)

    target = p["target_printer"]
    observed = {
        "authenticated_mqtt": True,
        "serial_match_verified": True,
        "firmware_version": target["firmware_version"],
        "host": target["host"],
        "material_loaded": p["material"],
    }
    report = preprint_readiness.prepare_print(
        MANIFEST, ROOT, p, job_path=sliced_job(tmp_path, p), observed=observed
    )
    assert report["checks"]["sliced_job"] == "PASS"
    assert report["checks"]["target_printer"] == "PASS"
    assert report["checks"]["material_loaded"] == "PASS"
    assert report["status"] == "BLOCKED"


def test_readiness_operator_gate_requires_every_other_check(monkeypatch):
    p = passport("oem_spindle_v03")
    target = p["target_printer"]
    monkeypatch.setattr(
        preprint_readiness,
        "assess_quality",
        lambda *_args: {"status": "PASS", "checks": []},
    )
    monkeypatch.setattr(
        preprint_readiness,
        "audit_passport",
        lambda *_args: {"status": "GCODE_VERIFIED_FIT_BLOCKED"},
    )
    observed = {
        "authenticated_mqtt": True,
        "serial_match_verified": True,
        "firmware_version": target["firmware_version"],
        "host": target["host"],
        "material_loaded": p["material"],
        "firmware_open_verified": True,
        "operator_approval": True,
    }
    for key in ("bed_clean", "nozzle_condition", "plate_clearance", "physical_fit"):
        observed[key] = True
        observed[key + "_evidence"] = f"{key}-photo"
    report = preprint_readiness.prepare_print(
        MANIFEST, ROOT, p, job_path="job.gcode.3mf", observed=observed
    )
    assert report["checks"]["operator_approval"] == "PASS"
    assert report["status"] == "READY"
    assert report["print_start_authorized"] is False


def test_plain_toolpath_still_passes_the_audit():
    assert gcode_audit._verify_toolpaths(gcode_body(), [256, 256, 256]) == (12, 12)


def test_compact_motion_word_is_bounds_checked():
    with pytest.raises(ValueError, match="motion outside bed"):
        gcode_audit._verify_toolpaths(gcode_body("G1X999Y5"), [256, 256, 256])


def test_compact_dangerous_firmware_command_is_rejected():
    with pytest.raises(ValueError, match="dangerous"):
        gcode_audit._verify_toolpaths(gcode_body("M112S0"), [256, 256, 256])


def test_relative_positioning_arcs_and_position_reset_are_not_accepted():
    bed = [256, 256, 256]
    with pytest.raises(ValueError, match="relative"):
        gcode_audit._verify_toolpaths(gcode_body("G91"), bed)
    with pytest.raises(ValueError, match="arc"):
        gcode_audit._verify_toolpaths(gcode_body("G2 X10 Y10 I5 J0"), bed)
    with pytest.raises(ValueError, match="position reset"):
        gcode_audit._verify_toolpaths(gcode_body("G92 X1000"), bed)
