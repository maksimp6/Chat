"""Dependency-free, offline regression tests for A1 mini 3MF render previews."""

from __future__ import annotations

import hashlib
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts.alice_a1mini_pla_cube_slice import cube_stl
from scripts.alice_a1mini_preview import audit_preview, embed_preview


REL = b"""<?xml version="1.0"?><Relationships>
<Relationship Target="/Metadata/plate_1.png"/>
<Relationship Target="/Metadata/plate_1_small.png"/>
</Relationships>"""
TYPES = b"""<Types><Default Extension="png" ContentType="image/png"/></Types>"""
SETTINGS = b"""<?xml version="1.0"?><config><plate>
<metadata key="gcode_file" value="Metadata/plate_1.gcode"/>
</plate></config>"""
GCODE = b"; HEADER_BLOCK_START\nG1 X10 Y10 E1\n"
MD5 = hashlib.md5(GCODE).hexdigest().encode("ascii")


def archive(
    path: Path,
    *,
    bad_rel: bool = False,
    no_gcode: bool = False,
    traversal: bool = False,
) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("_rels/.rels", b"<Relationships/>" if bad_rel else REL)
        z.writestr("[Content_Types].xml", TYPES)
        z.writestr("Metadata/model_settings.config", SETTINGS)
        if not no_gcode:
            z.writestr("Metadata/plate_1.gcode", GCODE)
        z.writestr("Metadata/plate_1.gcode.md5", MD5)
        if traversal:
            z.writestr("../other/path", b"not safe")


class PreviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "sliced.gcode.3mf"
        self.stl = Path(self.temp.name) / "cube.stl"
        self.stl.write_bytes(cube_stl())
        archive(self.path)

    def test_embeddings_are_real_and_gcode_md5_unchanged(self) -> None:
        first = embed_preview(self.path, self.stl)
        self.assertEqual(GCODE, zipfile.ZipFile(self.path).read("Metadata/plate_1.gcode"))
        self.assertEqual(MD5, zipfile.ZipFile(self.path).read("Metadata/plate_1.gcode.md5"))
        self.assertEqual(first, audit_preview(self.path))
        images = first["images"]
        self.assertEqual(
            set(images),
            {
                "Metadata/plate_1.png",
                "Metadata/plate_1_small.png",
                "Metadata/top_1.png",
            },
        )
        self.assertNotEqual(
            images["Metadata/plate_1.png"]["sha256"],
            images["Metadata/top_1.png"]["sha256"],
        )
        with zipfile.ZipFile(self.path) as z:
            self.assertIsNone(z.testzip())
            self.assertEqual(len(z.namelist()), len(set(z.namelist())))
            self.assertEqual(z.read("Metadata/plate_1.png")[:8], bytes.fromhex("89504e470d0a1a0a"))
        second = embed_preview(self.path, self.stl)
        self.assertEqual(first, second)

    def test_missing_thumbnails_are_rejected_before_staging(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "metadata"):
            audit_preview(self.path)

    def test_bad_relationship_rolls_back(self) -> None:
        archive(self.path, bad_rel=True)
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        with self.assertRaisesRegex(RuntimeError, "relationships"):
            embed_preview(self.path, self.stl)
        self.assertEqual(before, hashlib.sha256(self.path.read_bytes()).hexdigest())

    def test_missing_gcode_rolls_back(self) -> None:
        archive(self.path, no_gcode=True)
        original = self.path.read_bytes()
        with self.assertRaisesRegex(RuntimeError, "missing G-code"):
            embed_preview(self.path, self.stl)
        self.assertEqual(original, self.path.read_bytes())

    def test_zip_traversal_rejected(self) -> None:
        archive(self.path, traversal=True)
        original = self.path.read_bytes()
        with self.assertRaisesRegex(RuntimeError, "unsafe"):
            embed_preview(self.path, self.stl)
        self.assertEqual(original, self.path.read_bytes())

    def test_invalid_mesh_rejected_without_replacing_archive(self) -> None:
        original = self.path.read_bytes()
        bad = bytearray(self.stl.read_bytes())
        struct.pack_into("<f", bad, 84 + 12, float("nan"))
        self.stl.write_bytes(bad)
        with self.assertRaisesRegex(RuntimeError, "non-finite"):
            embed_preview(self.path, self.stl)
        self.assertEqual(original, self.path.read_bytes())

    def test_symlink_mesh_rejected(self) -> None:
        proxy = Path(self.temp.name) / "link.stl"
        proxy.symlink_to(self.stl)
        with self.assertRaisesRegex(RuntimeError, "symlink"):
            embed_preview(self.path, proxy)


if __name__ == "__main__":
    unittest.main()
