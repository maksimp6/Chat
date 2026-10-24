"""Dependency-free, offline regression tests for A1 mini slicer output validation.

The validators under test gate the Bambu Studio results before anything is staged.
Inputs are synthetic: the slicer is never run, and no printer or network is used.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import re
import struct
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Sequence
from pathlib import Path

from scripts import alice_a1mini_bagclip_slice as bagclip
from scripts.alice_a1mini_pla_cube_slice import (
    _read_sliced_archive,
    _validate_gcode,
    _validate_profile,
    _validate_result,
    audit,
)

NS_URI = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"

GOOD_PROFILE: dict[str, object] = {
    "filament_type": ["PLA"],
    "filament_density": ["1.24"],
    "printer_model": "Bambu Lab A1 mini",
    "printer_variant": "0.4",
    "curr_bed_type": "Textured PEI Plate",
    "nozzle_temperature": ["220"],
    "nozzle_temperature_initial_layer": ["220"],
    "textured_plate_temp": ["60"],
    "textured_plate_temp_initial_layer": ["60"],
}
GOOD_BAGCLIP_SETTINGS: dict[str, object] = {
    **GOOD_PROFILE,
    "wall_loops": "4",
    "sparse_infill_density": "25%",
}


def gcode_text(
    *,
    first_line: str = "; HEADER_BLOCK_START",
    layer_line: str | None = "; total layer number: 100",
    nozzle: str | None = "M109 S220",
    bed: str | None = "M190 S60",
    extrude: bool = True,
    extra: Sequence[str] = (),
) -> str:
    """Build G-code that passes the size gate unless a caller removes a required line."""
    lines = [first_line]
    lines += [line for line in (layer_line, nozzle, bed) if line is not None]
    lines += list(extra)
    lines.append("G28")
    for step in range(300):
        move = f"G1 X{10 + step % 150} Y20"
        lines.append(f"{move} E{0.01 * (step + 1):.4f} F1200" if extrude else f"{move} F1200")
    return "\n".join(lines) + "\n"


def model_object(
    *, triangles: int = 12, width: float = 20.0, depth: float = 20.0, height: float = 20.0
) -> dict[str, object]:
    return {
        "id": 3,
        "triangle_count": triangles,
        "bbox": {"width": width, "depth": depth, "height": height},
    }


def plate_entry(
    objects: list[dict[str, object]] | None = None, warning: str = ""
) -> dict[str, object]:
    return {
        "total_predication": 1234,
        "warning_message": warning,
        "objects": objects if objects is not None else [model_object()],
    }


def slicer_result(
    *,
    return_code: int = 0,
    error_string: str = "",
    plates: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    if plates is None:
        plates = [plate_entry()]
    return {"return_code": return_code, "error_string": error_string, "sliced_plates": plates}


def write_json(path: Path, data: object) -> Path:
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def write_sliced_3mf(path: Path, *, settings: object, gcode: str) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        if settings is not None:
            zf.writestr("Metadata/project_settings.config", json.dumps(settings))
        zf.writestr("Metadata/plate_1.gcode", gcode)


def source_model(
    *,
    counts: Sequence[int] = (1460, 1412, 1068),
    indices: tuple[int, int, int] = (0, 1, 2),
    transform: str = "1 0 0 0 1 0 0 0 1 20 20 0",
    vertices: Sequence[tuple[str, str, str]] = (
        ("0", "0", "0"),
        ("10", "0", "0"),
        ("0", "10", "0"),
    ),
) -> ET.Element:
    """Return a three-object 3MF model root with the requested triangle layout."""
    objects = []
    items = []
    vertex_xml = "".join(f'<vertex x="{x}" y="{y}" z="{z}"/>' for x, y, z in vertices)
    for number, count in enumerate(counts, start=1):
        triangle_xml = "".join(
            f'<triangle v1="{indices[0]}" v2="{indices[1]}" v3="{indices[2]}"/>'
            for _ in range(count)
        )
        objects.append(
            f'<object id="{number}" type="model"><mesh>'
            f"<vertices>{vertex_xml}</vertices><triangles>{triangle_xml}</triangles>"
            "</mesh></object>"
        )
        items.append(f'<item objectid="{number}" transform="{transform}"/>')
    resources = "".join(objects)
    build = "".join(items)
    text = (
        f'<model unit="millimeter" xmlns="{NS_URI}">'
        f"<resources>{resources}</resources><build>{build}</build></model>"
    )
    return ET.fromstring(text)


def write_source_3mf(path: Path, root: ET.Element) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("3D/3dmodel.model", ET.tostring(root))


class SlicerFixtureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)


class CubeValidationTests(SlicerFixtureTests):
    def test_accepts_verified_profile(self) -> None:
        _validate_profile(dict(GOOD_PROFILE))

    def test_rejects_unsafe_profile_values(self) -> None:
        unsafe = (
            ("printer_model", "Bambu Lab X1 Carbon"),
            ("printer_variant", "0.6"),
            ("filament_type", ["PETG"]),
            ("curr_bed_type", "Cool Plate"),
            ("nozzle_temperature", ["250"]),
            ("nozzle_temperature_initial_layer", ["250"]),
            ("textured_plate_temp", ["80"]),
            ("textured_plate_temp_initial_layer", ["80"]),
        )
        for key, value in unsafe:
            with self.subTest(key=key):
                settings = dict(GOOD_PROFILE)
                settings[key] = value
                with self.assertRaisesRegex(RuntimeError, rf"unsafe profile: {key}: expected"):
                    _validate_profile(settings)

    def test_rejects_missing_profile_key(self) -> None:
        settings = dict(GOOD_PROFILE)
        del settings["textured_plate_temp_initial_layer"]
        with self.assertRaisesRegex(
            RuntimeError,
            r"unsafe profile: textured_plate_temp_initial_layer: expected \['60'\], got None",
        ):
            _validate_profile(settings)

    def test_accepts_verified_gcode_and_parses_layer_count(self) -> None:
        self.assertEqual(100, _validate_gcode(gcode_text()))
        self.assertEqual(42, _validate_gcode(gcode_text(layer_line="; total layer number: 42")))

    def test_rejects_missing_or_wrong_temperature_commands(self) -> None:
        cases = (
            ("missing M109", {"nozzle": None}, "M109 S220"),
            ("commented M109", {"nozzle": "; M109 S220"}, "M109 S220"),
            ("M109 wrong target", {"nozzle": "M109 S215"}, "M109 S220"),
            ("M109 target prefix", {"nozzle": "M109 S2200"}, "M109 S220"),
            ("missing M190", {"bed": None}, "M190 S60"),
            ("commented M190", {"bed": "; M190 S60"}, "M190 S60"),
            ("M190 wrong target", {"bed": "M190 S65"}, "M190 S60"),
        )
        for label, overrides, command in cases:
            with (
                self.subTest(label),
                self.assertRaisesRegex(
                    RuntimeError, rf"missing verified {re.escape(command)} temperature"
                ),
            ):
                _validate_gcode(gcode_text(**overrides))

    def test_rejects_non_bambu_header_missing_layer_count_and_disagreeing_counts(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "not a Bambu Studio G-code header"):
            _validate_gcode(gcode_text(first_line="; generated by another slicer"))
        with self.assertRaisesRegex(RuntimeError, "no layer-count header"):
            _validate_gcode(gcode_text(layer_line=None))
        with self.assertRaisesRegex(RuntimeError, "layer-count headers disagree"):
            _validate_gcode(gcode_text(extra=["; total layer number: 99"]))

    def test_rejects_unexpectedly_small_gcode(self) -> None:
        small = "; HEADER_BLOCK_START\n; total layer number: 100\nM109 S220\nM190 S60\n"
        with self.assertRaisesRegex(RuntimeError, "unexpectedly small"):
            _validate_gcode(small)

    def test_accepts_single_cube_plate(self) -> None:
        result = write_json(self.root / "result.json", slicer_result())
        self.assertEqual(
            {"estimated_seconds": 1234, "model_object_id": 3},
            _validate_result(result),
        )

    def test_rejects_failed_or_unclean_slicer_results(self) -> None:
        cases = (
            (
                "nonzero return code",
                slicer_result(return_code=1, error_string="crash"),
                "slicer failed: crash",
            ),
            ("no plate", slicer_result(plates=[]), "expected exactly one sliced plate"),
            (
                "two plates",
                slicer_result(plates=[plate_entry(), plate_entry()]),
                "expected exactly one sliced plate",
            ),
            (
                "slicer warning",
                slicer_result(plates=[plate_entry(warning="Object is too close")]),
                "slicer warning: Object is too close",
            ),
            (
                "triangle count mismatch",
                slicer_result(plates=[plate_entry([model_object(triangles=11)])]),
                "cube geometry was not preserved",
            ),
            (
                "two objects",
                slicer_result(plates=[plate_entry([model_object(), model_object()])]),
                "cube geometry was not preserved",
            ),
            (
                "height out of range",
                slicer_result(plates=[plate_entry([model_object(height=18.0)])]),
                "unexpected model height",
            ),
            (
                "outside A1 mini build volume",
                slicer_result(plates=[plate_entry([model_object(width=200.0)])]),
                "invalid A1 mini build dimensions",
            ),
        )
        for label, result_data, message in cases:
            with self.subTest(label):
                result = write_json(self.root / "result.json", result_data)
                with self.assertRaisesRegex(RuntimeError, message):
                    _validate_result(result)

    def test_audit_writes_layer_count_parsed_from_gcode(self) -> None:
        sliced = self.root / "sliced.gcode.3mf"
        write_sliced_3mf(sliced, settings=GOOD_PROFILE, gcode=gcode_text())
        result = write_json(self.root / "result.json", slicer_result())
        report = audit(sliced, result)
        self.assertEqual(100, report["layers"])
        self.assertEqual(hashlib.sha256(sliced.read_bytes()).hexdigest(), report["sha256"])
        self.assertEqual(1234, report["estimated_seconds"])

    def test_audit_rejects_layer_count_that_differs_from_cube_height(self) -> None:
        sliced = self.root / "sliced.gcode.3mf"
        write_sliced_3mf(
            sliced,
            settings=GOOD_PROFILE,
            gcode=gcode_text(layer_line="; total layer number: 99"),
        )
        result = write_json(self.root / "result.json", slicer_result())
        with self.assertRaisesRegex(RuntimeError, r"G-code layer count 99 != expected 100"):
            audit(sliced, result)

    def test_sliced_archive_requires_settings_and_rejects_non_object_settings(self) -> None:
        missing = self.root / "missing-settings.3mf"
        write_sliced_3mf(missing, settings=None, gcode=gcode_text())
        with self.assertRaisesRegex(RuntimeError, "3MF is missing Metadata/project_settings"):
            _read_sliced_archive(missing)
        with self.assertRaisesRegex(RuntimeError, "did not produce the sliced 3MF"):
            _read_sliced_archive(self.root / "absent.3mf")
        non_object = self.root / "list-settings.3mf"
        write_sliced_3mf(non_object, settings=[1, 2], gcode=gcode_text())
        with self.assertRaisesRegex(TypeError, "invalid project settings JSON"):
            _read_sliced_archive(non_object)


class BagClipValidationTests(SlicerFixtureTests):
    def verify_plate(
        self,
        *,
        settings: dict[str, object] | None = None,
        gcode: str | None = None,
        result: dict[str, object] | None = None,
        face_count: int = 3940,
    ) -> tuple[dict[str, object], int]:
        merged = dict(GOOD_BAGCLIP_SETTINGS)
        merged.update(settings or {})
        dest = self.root / "bagclip.gcode.3mf"
        write_sliced_3mf(
            dest,
            settings=merged,
            gcode=gcode if gcode is not None else gcode_text(layer_line="; total layer number: 80"),
        )
        if result is None:
            result = slicer_result(
                plates=[plate_entry([model_object(triangles=3940, height=16.0)])]
            )
        result_path = write_json(self.root / "result.json", result)
        with contextlib.redirect_stdout(io.StringIO()):
            return bagclip._verified_plate(dest, result_path, face_count)

    def test_accepts_expected_three_island_source(self) -> None:
        root = source_model()
        geometry = bagclip._source_geometry(root)
        self.assertEqual({"1", "2", "3"}, set(geometry))
        bagclip._validate_build(root, geometry)

    def test_rejects_unexpected_source_triangle_counts(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "unexpected source triangle counts"):
            bagclip._source_geometry(source_model(counts=(1460, 1412, 1067)))

    def test_rejects_out_of_range_or_negative_triangle_indices(self) -> None:
        for indices in ((0, 1, 3), (-1, 1, 2)):
            with (
                self.subTest(indices=indices),
                self.assertRaisesRegex(RuntimeError, "invalid triangle indices"),
            ):
                bagclip._source_geometry(source_model(indices=indices))

    def test_rejects_non_finite_source_vertex(self) -> None:
        vertices = (("nan", "0", "0"), ("10", "0", "0"), ("0", "10", "0"))
        with self.assertRaisesRegex(RuntimeError, "non-finite source vertex"):
            bagclip._source_geometry(source_model(vertices=vertices))

    def test_rejects_missing_or_duplicate_build_items(self) -> None:
        root = source_model()
        build = root.find(f"{bagclip.NS}build")
        assert build is not None
        build.remove(list(build)[-1])
        with self.assertRaisesRegex(RuntimeError, "expected three build items"):
            bagclip._validate_build(root, bagclip._source_geometry(root))

        root = source_model()
        build = root.find(f"{bagclip.NS}build")
        assert build is not None
        list(build)[1].set("objectid", "1")
        with self.assertRaisesRegex(RuntimeError, "invalid or duplicate 3MF build object"):
            bagclip._validate_build(root, bagclip._source_geometry(root))

    def test_rejects_unsafe_build_transforms(self) -> None:
        cases = (
            ("scaled", "2 0 0 0 1 0 0 0 1 20 20 0", "unexpected source rotation or scaling"),
            ("rotated", "0 1 0 -1 0 0 0 0 1 20 20 0", "unexpected source rotation or scaling"),
            ("non-finite", "nan 0 0 0 1 0 0 0 1 20 20 0", "invalid object transform"),
            ("short", "1 0 0 0 1 0 0 0 1 20 20", "invalid object transform"),
            ("outside bed", "1 0 0 0 1 0 0 0 1 200 20 0", "outside A1 mini build volume"),
        )
        for label, transform, message in cases:
            with self.subTest(label):
                root = source_model(transform=transform)
                with self.assertRaisesRegex(RuntimeError, message):
                    bagclip._validate_build(root, bagclip._source_geometry(root))

    def test_rejects_source_size_or_hash_mismatch(self) -> None:
        path = self.root / "source.3mf"
        path.write_bytes(b"not the reviewed bag clip source")
        with self.assertRaisesRegex(RuntimeError, "source artifact changed: size/hash mismatch"):
            bagclip.validate_source(path)

    def test_merge_keeps_every_source_triangle(self) -> None:
        source = self.root / "source.3mf"
        write_source_3mf(source, source_model())
        destination = self.root / "islands.stl"
        with contextlib.redirect_stdout(io.StringIO()):
            total = bagclip.merge_plate_stl(source, destination)
        self.assertEqual(3940, total)  # 1460 + 1412 + 1068, no decimation
        data = destination.read_bytes()
        self.assertEqual(84 + 3940 * 50, len(data))
        self.assertEqual(
            b"Logos bag clip PR1113: three separated parts".ljust(80, b" "),
            data[:80],
        )
        self.assertEqual(3940, struct.unpack_from("<I", data, 80)[0])

    def test_merge_rejects_invalid_indices_without_writing_stl(self) -> None:
        for index, indices in enumerate(((-1, 1, 2), (0, 1, 3))):
            with self.subTest(indices=indices):
                source = self.root / f"bad-{index}.3mf"
                write_source_3mf(source, source_model(indices=indices))
                destination = self.root / f"never-{index}.stl"
                with (
                    self.assertRaisesRegex(RuntimeError, "invalid triangle indices"),
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    bagclip.merge_plate_stl(source, destination)
                self.assertFalse(destination.exists())

    def test_merge_rejects_degenerate_triangle(self) -> None:
        source = self.root / "degenerate.3mf"
        write_source_3mf(source, source_model(indices=(0, 0, 1)))
        with (
            self.assertRaisesRegex(RuntimeError, "degenerate triangle"),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            bagclip.merge_plate_stl(source, self.root / "degenerate.stl")

    def test_accepts_verified_bagclip_plate(self) -> None:
        plate, object_count = self.verify_plate()
        self.assertEqual(1, object_count)
        self.assertEqual(1234, plate["total_predication"])

    def test_rejects_unverified_bagclip_plate(self) -> None:
        extrusion_free = gcode_text(extrude=False, layer_line="; total layer number: 80")
        cases = (
            ("wall loops changed", {"settings": {"wall_loops": "2"}}, "four-wall/25-percent"),
            (
                "infill changed",
                {"settings": {"sparse_infill_density": "20%"}},
                "four-wall/25-percent",
            ),
            ("unsafe nozzle", {"settings": {"nozzle_temperature": ["240"]}}, "unsafe profile"),
            ("no extrusion", {"gcode": extrusion_free}, "no extrusion command"),
            (
                "nonzero result code",
                {"result": slicer_result(return_code=2, error_string="x")},
                "slicer result.json nonzero",
            ),
            (
                "slicer warning",
                {
                    "result": slicer_result(
                        plates=[
                            plate_entry(
                                [model_object(triangles=3940, height=16.0)], warning="Purge tower"
                            )
                        ]
                    )
                },
                "not exactly one plate or slicer warned",
            ),
            (
                "two plates",
                {
                    "result": slicer_result(
                        plates=[plate_entry([model_object(triangles=3940, height=16.0)])] * 2
                    )
                },
                "not exactly one plate or slicer warned",
            ),
            (
                "triangle count mismatch",
                {
                    "result": slicer_result(
                        plates=[plate_entry([model_object(triangles=3939, height=16.0)])]
                    )
                },
                "sliced-result triangle counts mismatch",
            ),
            (
                "height mismatch",
                {
                    "result": slicer_result(
                        plates=[plate_entry([model_object(triangles=3940, height=20.0)])]
                    )
                },
                "printed plate height differs from source",
            ),
        )
        for label, kwargs, message in cases:
            with self.subTest(label), self.assertRaisesRegex(RuntimeError, message):
                self.verify_plate(**kwargs)


if __name__ == "__main__":
    unittest.main()
