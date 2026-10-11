"""Offline tests of the visual object-recognition evaluator, not model accuracy."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.visual_object_contract import (
    evaluate,
    intersection_over_union,
    load_cases,
    verify_case,
)


TRUTH = [{"label": "spool", "bbox": [0.1, 0.1, 0.8, 0.8]}]
GOOD = [{"label": "spool", "bbox": [0.1, 0.1, 0.8, 0.8], "confidence": 0.98}]


class RecognitionTests(unittest.TestCase):
    def test_exact_match(self):
        self.assertEqual(intersection_over_union(TRUTH[0]["bbox"], GOOD[0]["bbox"]), 1)

    def test_nonoverlapping_boxes(self):
        self.assertEqual(intersection_over_union([0, 0, 0.2, 0.2], [0.8, 0.8, 1, 1]), 0)

    def test_one_to_one_matching(self):
        result = evaluate(TRUTH * 2, GOOD)
        self.assertEqual((result["tp"], result["fn"], result["fp"]), (1, 1, 0))

    def test_wrong_label(self):
        result = evaluate(TRUTH, [{**GOOD[0], "label": "motor"}])
        self.assertEqual((result["tp"], result["fn"], result["fp"]), (0, 1, 1))

    def test_low_confidence(self):
        result = evaluate(TRUTH, [{**GOOD[0], "confidence": 0.1}])
        self.assertEqual(result["tp"], 0)

    def test_invalid_coordinates(self):
        with self.assertRaises(ValueError):
            evaluate(TRUTH, [{**GOOD[0], "bbox": [-1, 0, 0.5, 0.5]}])

    def test_hallucinated_spindle_fails(self):
        case = {"objects": TRUTH, "forbidden_labels": ["cutting_spindle"]}
        result = verify_case(case, {"objects": GOOD + [
            {"label": "cutting_spindle", "bbox": [0.2, 0.2, 0.6, 0.6], "confidence": 0.9}
        ]})
        self.assertFalse(result["passed"])
        self.assertEqual(result["forbidden_found"], ["cutting_spindle"])

    def test_missing_objects_fails(self):
        self.assertFalse(verify_case({"objects": TRUTH}, {"objects": []})["passed"])

    def test_fixture_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "cases.json"
            p.write_text(json.dumps([{"id": "spool", "image": "sample.png", "objects": TRUTH}]))
            self.assertEqual(load_cases(p)[0]["id"], "spool")


if __name__ == "__main__":
    unittest.main()
