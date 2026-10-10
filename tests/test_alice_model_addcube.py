"""Offline tests for the first Alice Pro model tool."""

import hashlib
import math
import struct
import tempfile
import unittest
from pathlib import Path

from scripts.alice_model_addcube import model_addcube


class AddCubeTests(unittest.TestCase):
    def test_default_cube_is_deterministic(self):
        with tempfile.TemporaryDirectory() as directory:
            first = model_addcube(output_dir=directory)
            data = Path(first.output_path).read_bytes()
            second = model_addcube(output_dir=directory)
            self.assertEqual(first.sha256, second.sha256)
            self.assertEqual(first.sha256, hashlib.sha256(data).hexdigest())
            self.assertEqual(first.triangles, 12)
            self.assertEqual(len(data), 84 + 12 * 50)
            self.assertEqual(struct.unpack_from("<I", data, 80)[0], 12)
            vertices = [
                struct.unpack_from("<3f", data, 84 + i * 50 + j * 12 + 12)
                for i in range(12)
                for j in range(3)
            ]
            for axis in range(3):
                self.assertEqual(min(p[axis] for p in vertices), -10)
                self.assertEqual(max(p[axis] for p in vertices), 10)

    def test_custom_size(self):
        with tempfile.TemporaryDirectory() as directory:
            result = model_addcube(37, output_dir=directory)
            self.assertEqual(result.size_mm, 37)
            self.assertTrue(Path(result.output_path).exists())

    def test_invalid_dimensions(self):
        with tempfile.TemporaryDirectory() as directory:
            for value in (0, -1, 181, math.nan, math.inf, True, "20"):
                with (
                    self.subTest(value=value),
                    self.assertRaises((ValueError, TypeError)),
                ):
                    model_addcube(value, output_dir=directory)
            self.assertEqual(list(Path(directory).iterdir()), [])


if __name__ == "__main__":
    unittest.main()