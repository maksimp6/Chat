"""Versioned 3D assets: verify source bytes and safe source-only semantics."""

import ast
import hashlib
import json
import struct
from pathlib import Path

ASSETS = Path(__file__).resolve().parents[1] / "printing3d" / "assets"
MANIFEST = ASSETS / "models_manifest.json"


def _manifest():
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def test_original_source_members_are_immutable():
    manifest = _manifest()
    assert manifest["schema_version"] == 1
    assert len(manifest["models"]) == 3
    for model in manifest["models"]:
        assert len(model["source_archive_sha256"]) == 64
        assert model["status"] != "print_ready"
        for entry in model["members_manifest"]:
            path = ASSETS.parent.parent / entry["path"]
            raw = path.read_bytes()
            assert len(raw) == entry["bytes"]
            assert hashlib.sha256(raw).hexdigest() == entry["sha256"]
            assert ".." not in path.parts


def test_generator_sources_parse_without_execution():
    for path in ASSETS.glob("*/source/*.py.txt"):
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    assert len(list(ASSETS.glob("*/source/*.py.txt"))) == 4


def test_binary_stls_and_step_headers_are_valid():
    source_index = _manifest()["models"]
    stls = 0
    steps = 0
    for model in source_index:
        for entry in model["members_manifest"]:
            path = ASSETS.parent.parent / entry["path"]
            raw = path.read_bytes()
            if path.suffix.lower() == ".stl":
                stls += 1
                assert len(raw) >= 84
                count = struct.unpack_from("<I", raw, 80)[0]
                assert count == entry["geometry"]["triangles"]
                assert len(raw) == 84 + 50 * count
                assert entry["geometry"]["watertight"] is True
                assert all(0 < x <= 180 for x in entry["geometry"]["bounds_mm"])
            elif path.suffix.lower() == ".step":
                steps += 1
                assert raw.lstrip().startswith(b"ISO-10303-21;")
                assert b"END-ISO-10303-21;" in raw
    assert stls == 14
    assert steps == 3


def test_gallery_thumbnails_match_hashes_and_are_real_pngs():
    files = _manifest()["gallery"]["files"]
    assert len(files) == 7
    for entry in files:
        raw = (ASSETS.parent.parent / entry["path"]).read_bytes()
        assert raw[:8] == b"\x89PNG\r\n\x1a\n"
        assert len(raw) == entry["bytes"]
        assert hashlib.sha256(raw).hexdigest() == entry["sha256"]
        width, height = struct.unpack_from(">II", raw, 16)
        assert width >= 800 and height >= 450


def test_no_print_commands_or_restricted_game_assets_in_catalog():
    paths = [p.relative_to(ASSETS).as_posix().lower() for p in ASSETS.rglob("*") if p.is_file()]
    assert not any(name.endswith(".gcode") or name.endswith(".gcode.3mf") for name in paths)
    assert not any("ember_spirit" in name or "valve" in name for name in paths)
    assert not any(name.startswith("desk_spool_v02/parts/") for name in paths)
