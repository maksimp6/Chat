"""Single-file A1 mini sliced-3MF preflight; adversarial archives stay read-only."""

from __future__ import annotations

import hashlib
import io
import json
import os
import stat
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import pytest

from agent_skills.registry import SkillPolicyError, SkillRegistry
from printing3d import plate

GCODE = b"; valid test only\nM190 S60\nM109 S220\nG1 E.8 F1800\nG1 X5 Y3 E.03816\n"
SETTINGS = {
    "printer_model": "Bambu Lab A1 mini",
    "filament_type": ["PLA"],
    "nozzle_diameter": ["0.4"],
}


def build_3mf(
    tmp_path,
    *,
    name="test.gcode.3mf",
    settings=None,
    gcode=GCODE,
    sliced=True,
    digest=None,
    include_md5=True,
    include_model=True,
    extras=(),
):
    path = tmp_path / name
    configuration = SETTINGS if settings is None else settings
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as output:
        if include_model:
            output.writestr("3D/3dmodel.model", "<model/>")
        output.writestr("Metadata/project_settings.config", json.dumps(configuration))
        if sliced:
            output.writestr(plate.GCODE_NAME, gcode)
            if include_md5:
                output.writestr(
                    plate.GCODE_MD5_NAME,
                    digest if digest is not None else hashlib.md5(gcode).hexdigest(),
                )
        for member, content in extras:
            output.writestr(member, content)
    return path


def test_valid_single_file_pla_and_petg(tmp_path):
    a = build_3mf(tmp_path)
    report = plate.inspect_path(a)
    assert report["status"] == "ready_for_review"
    assert report["printer"] == "Bambu Lab A1 mini"
    assert report["material"] == "PLA"
    assert report["nozzle_target_c"] == 220
    assert report["bed_target_c"] == 60
    assert report["extrusion_commands"] == 2
    assert report["sha256"] == hashlib.sha256(a.read_bytes()).hexdigest()
    assert report["gcode_sha256"] == hashlib.sha256(GCODE).hexdigest()

    petg = build_3mf(
        tmp_path,
        name="petg.gcode.3mf",
        settings={**SETTINGS, "filament_type": ["PETG"]},
        gcode=b"M190 S70\nM109 S245\nG1 X2 E.1\n",
    )
    b = plate.inspect_path(petg)
    assert b["status"] == "ready_for_review"
    assert (b["material"], b["nozzle_target_c"], b["bed_target_c"]) == ("PETG", 245, 70)


def test_source_only_is_not_ready_and_cannot_masquerade_as_slice(tmp_path):
    original = build_3mf(tmp_path, name="source.3mf", sliced=False)
    result = plate.inspect_path(original)
    assert result["status"] == "needs_slicing"
    assert "sliced" in result["reason"]
    sliced_wrong_name = build_3mf(tmp_path, name="sliced.3mf")
    assert plate.inspect_path(sliced_wrong_name)["status"] == "blocked"


@pytest.mark.parametrize(
    "settings,reason",
    [
        ({**SETTINGS, "printer_model": "Bambu Lab P1S"}, "printer profile"),
        ({**SETTINGS, "filament_type": ["ABS"]}, "filament"),
        ({**SETTINGS, "filament_type": ["PLA", "PETG"]}, "filament"),
        ({**SETTINGS, "nozzle_diameter": ["0.6"]}, "nozzle"),
        ({**SETTINGS, "nozzle_diameter": ["nan"]}, "nozzle"),
        ({**SETTINGS, "nozzle_diameter": []}, "nozzle"),
    ],
)
def test_wrong_profile_is_blocked(tmp_path, settings, reason):
    path = build_3mf(tmp_path, settings=settings)
    result = plate.inspect_path(path)
    assert result["status"] == "blocked"
    assert reason in result["reason"]


@pytest.mark.parametrize(
    "gcode,reason",
    [
        (b"M109 S220\nG1 E.1\n", "heat-and-wait"),
        (b"M190 S60\nG1 E.1\n", "heat-and-wait"),
        (b"M190 S60\nM109 S245\nG1 E.1\n", "nozzle temperature"),
        (b"M190 S90\nM109 S220\nG1 E.1\n", "bed temperature"),
        (b"; M109 S220\n; M190 S60\nG1 E.1\n", "heat-and-wait"),
        (b"M190 S60\nM109 S220\nG1 X1 Y1\n", "printable extrusion"),
        (b"M190 S60\nM109 S220\n" + b"G1 X" + b"2" * 17000 + b"\nG1 E.1\n", "line exceeds"),
        (b"M190 S60\nM109 S-2\nG1 E.1\n", "temperature"),
    ],
)
def test_gcode_limits_are_fail_closed(tmp_path, gcode, reason):
    path = build_3mf(tmp_path, gcode=gcode)
    result = plate.inspect_path(path)
    assert result["status"] == "blocked"
    assert reason in result["reason"]


@pytest.mark.parametrize(
    "keyword,expected",
    [
        ({"digest": "0" * 32}, "checksum mismatch"),
        ({"digest": "bad hash"}, "invalid G-code integrity"),
        ({"digest": "0" * 100}, "integrity checksum exceeds size limit"),
        ({"include_md5": False}, "missing sliced G-code"),
        ({"include_model": False}, "missing 3MF model"),
    ],
)
def test_integrity_and_required_parts(tmp_path, keyword, expected):
    path = build_3mf(tmp_path, **keyword)
    report = plate.inspect_path(path)
    assert report["status"] == "blocked"
    assert expected in report["reason"]


def test_zip_path_traversal_and_duplicate_entry_rejected(tmp_path):
    path = build_3mf(tmp_path, extras=[("../outside", "bad")])
    assert "unsafe" in plate.inspect_path(path)["reason"]
    target = build_3mf(tmp_path, name="dup.gcode.3mf")
    with ZipFile(target, "a") as archive:
        with pytest.warns(UserWarning, match="Duplicate name"):
            archive.writestr("Metadata/project_settings.config", "duplicate")
    assert "duplicated" in plate.inspect_path(target)["reason"]


def test_zip_symlink_and_bomb_guard(tmp_path):
    link = ZipInfo("evil")
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    p = build_3mf(tmp_path, name="link.gcode.3mf", extras=[(link, "to-something")])
    assert "unsafe" in plate.inspect_path(p)["reason"]
    bomb = build_3mf(tmp_path, name="bomb.gcode.3mf", extras=[("compressed", b"0" * 2_000_000)])
    assert "decompression limit" in plate.inspect_path(bomb)["reason"]


def test_bad_zip_oversize_and_name_refused(tmp_path, monkeypatch):
    corrupted = tmp_path / "corrupted.gcode.3mf"
    corrupted.write_bytes(b"not a ZIP")
    assert plate.inspect_path(corrupted)["status"] == "blocked"
    with corrupted.open("rb") as stream:
        assert plate.inspect_archive(stream, "renamed.zip")["status"] == "blocked"
    good = build_3mf(tmp_path, name="tiny.gcode.3mf")
    monkeypatch.setattr(plate, "MAX_FILE_BYTES", 8)
    assert "size limit" in plate.inspect_path(good)["reason"]


def test_registry_exposes_two_3d_skills_with_role_isolation():
    registry = SkillRegistry()
    names = {item["name"] for item in registry.catalog(role="alice")}
    assert {"3d-print-preflight", "a1-mini-print-watch"} <= names
    assert registry.load("3d-print-preflight", role="alice").body
    assert registry.load("a1-mini-print-watch", role="alice").body
    with pytest.raises(SkillPolicyError):
        registry.load("a1-mini-print-watch", role="security-reviewer")


def test_sliced_3mf_validator_never_writes_to_source(tmp_path):
    source = build_3mf(tmp_path)
    before = (source.read_bytes(), source.stat().st_mtime_ns)
    for _ in range(3):
        assert plate.inspect_path(source)["status"] == "ready_for_review"
    assert (source.read_bytes(), source.stat().st_mtime_ns) == before


def test_tool_entry_and_owner_scoped_inspection(tmp_path, monkeypatch):
    pytest.importorskip("flask")
    from printing3d import tools

    owner = "alice-owner"
    other = "other-owner"
    artifact = "fit.gcode.3mf"
    a = tmp_path / hashlib.sha256(owner.encode()).hexdigest()
    b = tmp_path / hashlib.sha256(other.encode()).hexdigest()
    a.mkdir()
    b.mkdir()
    build_3mf(a, name=artifact)
    build_3mf(b, name=artifact, settings={**SETTINGS, "printer_model": "other"})
    monkeypatch.setenv("ALICE_3D_ARTIFACT_ROOT", str(tmp_path))
    monkeypatch.setattr(tools, "_trusted_owner", lambda cfg: owner)
    result = tools.plate_inspect({"artifact_name": artifact, "owner_id": other})
    assert result["status"] == "ready_for_review"
    assert result["printer"] == "Bambu Lab A1 mini"
    definition = tools.PRINTING3D_TOOLS["printing3d.plate.inspect"]
    assert definition["read_only"] is True
    assert definition["requires_approval"] is False

    monkeypatch.setattr(tools, "_trusted_owner", lambda cfg: "missing-owner")
    assert tools.plate_inspect({"artifact_name": artifact})["status"] == "not_found"

    monkeypatch.setattr(tools, "_trusted_owner", lambda cfg: owner)
    for name in ("../../fit.gcode.3mf", "/etc/passwd", "x.zip", "..gcode.3mf"):
        with pytest.raises(ValueError):
            tools.plate_inspect({"artifact_name": name})
    (a / "alias.gcode.3mf").symlink_to(b / artifact)
    assert tools.plate_inspect({"artifact_name": "alias.gcode.3mf"})["status"] == "blocked"
    monkeypatch.delenv("ALICE_3D_ARTIFACT_ROOT")
    assert tools.plate_inspect({"artifact_name": artifact})["status"] == "needs_configuration"


def test_many_members_and_config_limits(tmp_path, monkeypatch):
    path = build_3mf(
        tmp_path,
        name="many.gcode.3mf",
        extras=[(f"other-{i}", "a") for i in range(plate.MAX_ENTRIES)],
    )
    assert "too many" in plate.inspect_path(path)["reason"]

    normal = build_3mf(tmp_path, name="config.gcode.3mf")
    monkeypatch.setattr(plate, "MAX_CONFIG_BYTES", 8)
    assert "configuration exceeds" in plate.inspect_path(normal)["reason"]


def test_invalid_json_and_nozzle_string(tmp_path):
    bad = tmp_path / "broken.gcode.3mf"
    with ZipFile(bad, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr(plate.MODEL_NAME, "<model/>")
        archive.writestr(plate.CONFIG_NAME, "{incomplete")
        archive.writestr(plate.GCODE_NAME, GCODE)
        archive.writestr(plate.GCODE_MD5_NAME, hashlib.md5(GCODE).hexdigest())
    assert "invalid project configuration" in plate.inspect_path(bad)["reason"]

    other = build_3mf(
        tmp_path,
        name="nonnumeric.gcode.3mf",
        settings={**SETTINGS, "nozzle_diameter": ["not-a-number"]},
    )
    assert "invalid nozzle diameter" in plate.inspect_path(other)["reason"]


def test_sliced_plate_count_and_empty_payload(tmp_path):
    multiple = build_3mf(
        tmp_path,
        name="multiple.gcode.3mf",
        extras=[("Metadata/plate_2.gcode", b"G1 X2 E1\n")],
    )
    assert "multiple or ambiguous" in plate.inspect_path(multiple)["reason"]
    empty = build_3mf(tmp_path, name="empty.gcode.3mf", gcode=b"")
    assert "empty or oversized" in plate.inspect_path(empty)["reason"]


def test_unknown_commands_and_s0_do_not_invent_heat(tmp_path):
    path = build_3mf(
        tmp_path,
        name="off.gcode.3mf",
        gcode=b"G28\nM104\nM104 S0\nM140 S0\nM109 S220\nM190 S60\nG1 E.1\n",
    )
    result = plate.inspect_path(path)
    assert result["status"] == "ready_for_review"
    assert result["extrusion_commands"] == 1
    assert plate._parse_temperature(b"T0", "PLA") is None
    assert plate._parse_temperature(b"M104", "PLA") is None


def test_compressed_archive_limit_and_decompression_budget(tmp_path, monkeypatch):
    target = build_3mf(tmp_path, name="limit.gcode.3mf")
    monkeypatch.setattr(plate, "MAX_ARCHIVE_UNCOMPRESSED", 10)
    assert "decompression limit" in plate.inspect_path(target)["reason"]


def test_absolute_archive_path_and_encryption_bit_are_rejected(tmp_path):
    absolute = build_3mf(tmp_path, name="absolute.gcode.3mf", extras=[("/etc/passwd", "x")])
    assert "unsafe" in plate.inspect_path(absolute)["reason"]
    target = build_3mf(tmp_path, name="encrypted-flag.gcode.3mf")
    with ZipFile(target) as archive:
        archive.infolist()[0].flag_bits |= 1
        with pytest.raises(plate.PlateError, match="unsafe"):
            plate._preflight_members(archive)


def test_main_emits_json_and_exit_code_without_writes(tmp_path, monkeypatch, capsys):
    import sys

    target = build_3mf(tmp_path, name="cli.gcode.3mf")
    monkeypatch.setattr(sys, "argv", ["3d-plate-preflight", str(target)])
    assert plate.main() == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ready_for_review"

    source = build_3mf(tmp_path, name="cli-source.3mf", sliced=False)
    monkeypatch.setattr(sys, "argv", ["3d-plate-preflight", str(source)])
    assert plate.main() == 2
    assert json.loads(capsys.readouterr().out)["status"] == "needs_slicing"


def test_module_entrypoint_executes_read_only_cli(tmp_path, monkeypatch, capsys):
    import runpy
    import sys

    artifact = build_3mf(tmp_path, name="entrypoint.gcode.3mf")
    monkeypatch.setattr(sys, "argv", ["printing3d.plate", str(artifact)])
    with pytest.warns(RuntimeWarning, match="found in sys.modules"):
        with pytest.raises(SystemExit) as exc:
            runpy.run_module("printing3d.plate", run_name="__main__")
    assert exc.value.code == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ready_for_review"


def test_tool_denies_nonregular_oversize_and_wrong_owner(tmp_path, monkeypatch):
    pytest.importorskip("flask")
    from types import SimpleNamespace

    from printing3d import tools

    owner_id = "review-owner"
    trusted_context = {"_universal_context": {"call": SimpleNamespace(user_id=owner_id)}}
    owner_dir = tmp_path / hashlib.sha256(owner_id.encode()).hexdigest()
    owner_dir.mkdir()
    name = "job.gcode.3mf"
    path = build_3mf(owner_dir, name=name)
    monkeypatch.setenv("ALICE_3D_ARTIFACT_ROOT", str(tmp_path))
    # Do not trust a model-provided owner_id, folder or arbitrary file path.
    assert (
        tools.plate_inspect({"artifact_name": name, "owner_id": "attacker"}, trusted_context)[
            "status"
        ]
        == "ready_for_review"
    )
    assert (
        tools.plate_inspect(
            {"artifact_name": name},
            {"_universal_context": {"call": SimpleNamespace(user_id="other")}},
        )["status"]
        == "not_found"
    )

    monkeypatch.setattr(tools, "MAX_FILE_BYTES", 8)
    assert tools.plate_inspect({"artifact_name": name}, trusted_context)["status"] == "blocked"
    monkeypatch.undo()
    monkeypatch.setenv("ALICE_3D_ARTIFACT_ROOT", str(tmp_path))
    folder = owner_dir / "folder.gcode.3mf"
    folder.mkdir()
    assert (
        tools.plate_inspect({"artifact_name": folder.name}, trusted_context)["status"] == "blocked"
    )
    assert path.is_file()
    for raw in (None, 17, "bad.zip", "../escape.gcode.3mf"):
        with pytest.raises(ValueError):
            tools.plate_inspect({"artifact_name": raw}, trusted_context)

    symlink_root = tmp_path.parent / (tmp_path.name + "-link")
    symlink_root.symlink_to(tmp_path, target_is_directory=True)
    monkeypatch.setenv("ALICE_3D_ARTIFACT_ROOT", str(symlink_root))
    assert tools.plate_inspect({"artifact_name": name}, trusted_context)["status"] == "blocked"


@pytest.mark.parametrize("command", [b"M500", b"M502", b"M997", b"M112"])
def test_printer_maintenance_commands_never_pass_preflight(tmp_path, command):
    gcode = b"M190 S60\nM109 S220\nG1 X2 E.1\n" + command + b"\n"
    path = build_3mf(tmp_path, gcode=gcode)
    assert "prohibited" in plate.inspect_path(path)["reason"]
