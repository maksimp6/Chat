"""Fast, dependency-free integrity checks for versioned Alice 3D source assets."""

import hashlib
import json
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "printing3d" / "assets"
CATALOG = ASSETS / "catalog.json"

ORIGINALS = {
    "parametric/phone_stand.scad": (
        1555,
        "556b9992725142d45c953b0c1bb5f293a69aea066f07e0e3bdaa94e0b6f84d8f",
    ),
    "parametric/sauce_stand.scad": (
        1829,
        "c951711e947b143fb197917dfe734da10c431cad6f77070ec6a80fa59f767f27",
    ),
    "parametric/test_tube_holder.scad": (
        838,
        "263b2bffa4648e23acfaed335755392902c71ecd31590e550ae2b52ed537a36c",
    ),
    "parametric/test_tube_holder.3mf": (
        10532,
        "76f82a5aca96b44e96334278a903a188235ed4d9e96f1f0f88be3d899d3681c4",
    ),
}


def _catalog() -> dict:
    return json.loads(CATALOG.read_text(encoding="utf-8"))


def test_catalog_schema_and_unique_assets() -> None:
    data = _catalog()
    assert data["schema_version"] == 1
    assets = data["assets"]
    ids = [asset["id"] for asset in assets]
    assert len(ids) == len(set(ids))
    by_id = {asset["id"]: asset for asset in assets}
    for asset in assets:
        assert re.fullmatch(r"[0-9a-f]{64}", asset["sha256"])
        assert asset["bytes"] > 0
        if "duplicate_of" in asset:
            target = by_id[asset["duplicate_of"]]
            assert target["sha256"] == asset["sha256"]
            assert target["bytes"] == asset["bytes"]


def test_committed_assets_match_original_byte_hashes() -> None:
    data = _catalog()
    files = {asset["path"]: asset for asset in data["assets"] if asset["location"] == "repo"}
    assert set(files) == {"printing3d/assets/" + path for path in ORIGINALS}
    for relative, (size, sha256) in ORIGINALS.items():
        path = ASSETS / relative
        assert path.is_file()
        raw = path.read_bytes()
        assert len(raw) == size
        assert hashlib.sha256(raw).hexdigest() == sha256
        asset = files["printing3d/assets/" + relative]
        assert asset["bytes"] == size
        assert asset["sha256"] == sha256


def test_unsliced_3mf_is_safe_but_not_gcode() -> None:
    path = ASSETS / "parametric" / "test_tube_holder.3mf"
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        assert sorted(names) == sorted(["3D/3dmodel.model", "_rels/.rels", "[Content_Types].xml"])
        assert archive.testzip() is None
        assert all(not name.startswith("/") and ".." not in name.split("/") for name in names)
        assert not any(name.lower().endswith(".gcode") for name in names)
        assert sum(info.file_size for info in archive.infolist()) < 1_000_000
        model = ElementTree.fromstring(archive.read("3D/3dmodel.model"))
    assert model.attrib.get("unit") == "millimeter"
    assert not (ASSETS / "parametric" / "test_tube_holder.gcode.3mf").exists()


def test_dota_artifacts_never_committed_in_catalog() -> None:
    for asset in _catalog()["assets"]:
        if asset["id"].startswith("ember-"):
            assert asset["location"] != "repo"
            assert asset["redistribution"] == "blocked_pending_ip_review"
