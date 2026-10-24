"""Offline object-detection contracts: class, bounding box, and abstention.

Images and predictions are independent inputs; this module never calls a model.
Bounding boxes use normalized [x0, y0, x1, y1] coordinates in [0, 1].
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_MAX_OBJECTS = 64


def _valid_name(value: object) -> bool:
    return isinstance(value, str) and _NAME.fullmatch(value) is not None


def _valid_box(box: object) -> bool:
    return (
        isinstance(box, list)
        and len(box) == 4
        and all(
            isinstance(v, (int, float))
            and not isinstance(v, bool)
            and math.isfinite(v)
            and 0 <= v <= 1
            for v in box
        )
        and box[0] < box[2]
        and box[1] < box[3]
    )


def _validate_object(obj: object, *, ground_truth: bool = False) -> None:
    if not isinstance(obj, dict) or not _valid_name(obj.get("label")):
        raise ValueError("object must have a canonical lower-case label")
    if not _valid_box(obj.get("bbox")):
        raise ValueError("bounding box must be normalized and non-degenerate")
    if not ground_truth:
        confidence = obj.get("confidence")
        if (
            not isinstance(confidence, (int, float))
            or isinstance(confidence, bool)
            or not math.isfinite(confidence)
            or not 0 <= confidence <= 1
        ):
            raise ValueError("prediction confidence must be a number in [0, 1]")


def _validate_objects(objects: object, *, ground_truth: bool = False) -> list[dict]:
    if not isinstance(objects, list) or len(objects) > _MAX_OBJECTS:
        raise ValueError("objects must be a bounded list")
    for obj in objects:
        _validate_object(obj, ground_truth=ground_truth)
    return objects


def _validate_case_metadata(case: dict) -> None:
    if case.get("annotation_status", "provisional") not in {"provisional", "reviewed"}:
        raise ValueError("invalid annotation review status")
    if case.get("source_kind", "cad_render") not in {"cad_render", "physical_photo"}:
        raise ValueError("invalid source kind")
    if set(case.get("forbidden_labels", [])) & {o["label"] for o in case["objects"]}:
        raise ValueError("ground truth conflicts with forbidden labels")


def _validate_case(case: object) -> None:
    if not isinstance(case, dict) or not _valid_name(case.get("id")):
        raise ValueError("case needs a canonical unique id")
    if not isinstance(case.get("image"), str) or not case["image"]:
        raise ValueError("case must specify image filename")
    _validate_objects(case.get("objects"), ground_truth=True)
    for field in ("forbidden_labels", "forbidden_claims"):
        values = case.get(field, [])
        if not isinstance(values, list) or any(not _valid_name(v) for v in values):
            raise ValueError(f"invalid {field}")
    _validate_case_metadata(case)
    if "crop" in case:
        box = case["crop"]
        if (
            not isinstance(box, list)
            or len(box) != 4
            or any(not isinstance(v, int) or isinstance(v, bool) or v < 0 for v in box)
            or box[0] >= box[2]
            or box[1] >= box[3]
        ):
            raise ValueError("pixel crop must be [left, top, right, bottom]")
    if "image_sha256" in case and (
        not isinstance(case["image_sha256"], str)
        or not re.fullmatch(r"[0-9a-f]{64}", case["image_sha256"])
    ):
        raise ValueError("image_sha256 must be a lowercase digest")


def load_cases(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not data or len(data) > 200:
        raise ValueError("visual cases must be a bounded nonempty array")
    ids = set()
    for case in data:
        _validate_case(case)
        if case["id"] in ids:
            raise ValueError("duplicate visual case ID")
        ids.add(case["id"])
    return data


def intersection_over_union(a: list, b: list) -> float:
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    area = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    return area / (area_a + area_b - area)


def _augment(
    truth_index: int,
    choices: list[list[int]],
    assignments: dict[int, int],
    seen: set[int],
) -> bool:
    for prediction_index in choices[truth_index]:
        if prediction_index in seen:
            continue
        seen.add(prediction_index)
        old_truth = assignments.get(prediction_index)
        if old_truth is None or _augment(old_truth, choices, assignments, seen):
            assignments[prediction_index] = truth_index
            return True
    return False


def evaluate(
    expected: list[dict],
    predicted: list[dict],
    *,
    min_iou: float = 0.5,
    min_confidence: float = 0.5,
) -> dict:
    """Maximum-cardinality one-to-one matching; false positives are not hidden."""
    _validate_objects(expected, ground_truth=True)
    _validate_objects(predicted)
    if not (0 < min_iou <= 1 and 0 <= min_confidence <= 1):
        raise ValueError("invalid evaluator thresholds")
    candidates = []
    for truth in expected:
        scored = [
            (intersection_over_union(truth["bbox"], pred["bbox"]), i)
            for i, pred in enumerate(predicted)
            if truth["label"] == pred["label"] and pred["confidence"] >= min_confidence
        ]
        candidates.append(
            [
                index
                for iou, index in sorted(scored, key=lambda item: (-item[0], item[1]))
                if iou >= min_iou
            ]
        )
    assignments: dict[int, int] = {}
    for truth_index in sorted(range(len(expected)), key=lambda i: (len(candidates[i]), i)):
        _augment(truth_index, candidates, assignments, set())
    matches = [
        {
            "label": expected[i]["label"],
            "truth_index": i,
            "prediction_index": j,
            "iou": round(intersection_over_union(expected[i]["bbox"], predicted[j]["bbox"]), 4),
        }
        for j, i in sorted(assignments.items())
    ]
    tp = len(assignments)
    fn = len(expected) - tp
    fp = len(predicted) - tp
    precision = tp / (tp + fp) if tp + fp else float(fn == 0)
    recall = tp / (tp + fn) if tp + fn else 1.0
    f1 = (2 * precision * recall / (precision + recall)) if precision + recall else 0.0
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": round(precision, 6),
        "recall": round(recall, 6),
        "f1": round(f1, 6),
        "matches": matches,
    }


def verify_case(case: dict, prediction: dict) -> dict:
    """Pass only if all target objects match and there are no invented claims."""
    _validate_case(case)
    if not isinstance(prediction, dict):
        raise TypeError("recognizer must return a JSON object")
    objects = _validate_objects(prediction.get("objects"))
    claims = prediction.get("claims", [])
    if not isinstance(claims, list) or len(claims) > 64 or any(not _valid_name(c) for c in claims):
        raise ValueError("recognizer claims must be canonical names")
    report = evaluate(case["objects"], objects)
    report["forbidden_found"] = sorted(
        {obj["label"] for obj in objects} & set(case.get("forbidden_labels", []))
    )
    report["unsupported_claims"] = sorted(set(claims) & set(case.get("forbidden_claims", [])))
    report["passed"] = (
        report["fn"] == 0
        and report["fp"] == 0
        and not report["forbidden_found"]
        and not report["unsupported_claims"]
    )
    return report
