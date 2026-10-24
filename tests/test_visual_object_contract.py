"""Deterministic evaluator tests: labels, IoU, abstention and hallucination gates."""

from __future__ import annotations

import copy
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

TRUTH = [{"label": "spool_holder", "bbox": [0.1, 0.1, 0.8, 0.8]}]
GOOD = [{"label": "spool_holder", "bbox": [0.1, 0.1, 0.8, 0.8], "confidence": 0.98}]


def fixture(*, objects=None):
    return {
        "id": "sample",
        "image": "sample.png",
        "objects": copy.deepcopy(TRUTH if objects is None else objects),
        "forbidden_labels": ["cutting_spindle", "electric_motor"],
        "forbidden_claims": ["motor_500w", "physically_printed"],
    }


class RecognitionTests(unittest.TestCase):
    def test_exact_match(self):
        self.assertEqual(intersection_over_union(TRUTH[0]["bbox"], GOOD[0]["bbox"]), 1)

    def test_nonoverlapping_boxes(self):
        self.assertEqual(intersection_over_union([0, 0, 0.2, 0.2], [0.8, 0.8, 1, 1]), 0)

    def test_one_to_one_matching(self):
        result = evaluate(TRUTH * 2, GOOD)
        self.assertEqual((result["tp"], result["fn"], result["fp"]), (1, 1, 0))

    def test_maximum_cardinality_reassigns_early_match(self):
        left = {"label": "shell", "bbox": [0.1, 0.1, 0.6, 0.6]}
        right = {"label": "shell", "bbox": [0.3, 0.1, 0.8, 0.6]}
        shared = {"label": "shell", "bbox": [0.2, 0.1, 0.7, 0.6], "confidence": 0.9}
        left_only = {"label": "shell", "bbox": [0.0, 0.1, 0.5, 0.6], "confidence": 0.9}
        self.assertEqual(evaluate([left, right], [shared, left_only])["tp"], 2)

    def test_wrong_label(self):
        result = evaluate(TRUTH, [{**GOOD[0], "label": "electric_motor"}])
        self.assertEqual((result["tp"], result["fn"], result["fp"]), (0, 1, 1))

    def test_low_confidence(self):
        self.assertEqual(evaluate(TRUTH, [{**GOOD[0], "confidence": 0.1}])["tp"], 0)

    def test_boolean_confidence_rejected(self):
        with self.assertRaises(ValueError):
            evaluate(TRUTH, [{**GOOD[0], "confidence": True}])

    def test_nan_confidence_rejected(self):
        with self.assertRaises(ValueError):
            evaluate(TRUTH, [{**GOOD[0], "confidence": float("nan")}])

    def test_invalid_coordinates(self):
        with self.assertRaises(ValueError):
            evaluate(TRUTH, [{**GOOD[0], "bbox": [-1, 0, 0.5, 0.5]}])

    def test_reversed_box_rejected(self):
        with self.assertRaises(ValueError):
            evaluate(TRUTH, [{**GOOD[0], "bbox": [0.8, 0, 0.1, 0.5]}])

    def test_invalid_threshold_rejected(self):
        with self.assertRaises(ValueError):
            evaluate(TRUTH, GOOD, min_iou=0)

    def test_hallucinated_spindle_fails(self):
        case = fixture()
        detection = {"label": "cutting_spindle", "bbox": [0.2, 0.2, 0.6, 0.6], "confidence": 0.9}
        result = verify_case(case, {"objects": GOOD + [detection]})
        self.assertFalse(result["passed"])
        self.assertEqual(result["forbidden_found"], ["cutting_spindle"])

    def test_unsupported_motor_power_claim_fails(self):
        result = verify_case(fixture(), {"objects": GOOD, "claims": ["motor_500w"]})
        self.assertFalse(result["passed"])
        self.assertEqual(result["unsupported_claims"], ["motor_500w"])

    def test_missing_objects_fails(self):
        self.assertFalse(verify_case(fixture(), {"objects": []})["passed"])

    def test_correct_abstention_on_blank_crop(self):
        report = verify_case(fixture(objects=[]), {"objects": [], "claims": []})
        self.assertTrue(report["passed"])
        self.assertEqual((report["tp"], report["fp"], report["fn"]), (0, 0, 0))

    def test_hallucination_on_blank_crop_fails(self):
        result = verify_case(fixture(objects=[]), {"objects": GOOD})
        self.assertFalse(result["passed"])
        self.assertEqual(result["fp"], 1)

    def test_prediction_requires_list(self):
        with self.assertRaises(ValueError):
            verify_case(fixture(), {"objects": "not a list"})

    def test_unknown_prediction_label_rejected(self):
        with self.assertRaises(ValueError):
            verify_case(fixture(), {"objects": [{**GOOD[0], "label": "I saw a motor!"}]})

    def test_reject_many_objects(self):
        with self.assertRaises(ValueError):
            evaluate(TRUTH, GOOD * 65)

    def test_fixture_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "cases.json"
            blank = fixture(objects=[])
            blank["id"] = "blank"
            p.write_text(json.dumps([fixture(), blank]))
            self.assertEqual(len(load_cases(p)), 2)

    def test_duplicate_ids_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "cases.json"
            p.write_text(json.dumps([fixture(), fixture()]))
            with self.assertRaisesRegex(ValueError, "duplicate"):
                load_cases(p)

    def test_invalid_sha_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "cases.json"
            item = fixture()
            item["image_sha256"] = "bad"
            p.write_text(json.dumps([item]))
            with self.assertRaisesRegex(ValueError, "image_sha256"):
                load_cases(p)


if __name__ == "__main__":
    unittest.main()
