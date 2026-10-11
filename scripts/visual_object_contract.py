"""Ground-truth visual recognition contracts for photographed and rendered objects.

A recognizer must output JSON with objects containing label, bbox [x1,y1,x2,y2]
in normalized 0..1 coordinates, and confidence in 0..1.
"""

from __future__ import annotations

import json
from pathlib import Path


def load_cases(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not data:
        raise ValueError("visual cases must be a nonempty array")
    ids = set()
    for case in data:
        if case["id"] in ids:
            raise ValueError("duplicate visual case ID")
        ids.add(case["id"])
        if not case.get("image") or not case.get("objects"):
            raise ValueError("missing image or ground-truth objects")
        for obj in case["objects"]:
            _validate_object(obj, ground_truth=True)
    return data


def _validate_object(obj: dict, *, ground_truth: bool = False) -> None:
    if not isinstance(obj, dict) or not isinstance(obj.get("label"), str):
        raise ValueError("object must have a label")
    box = obj.get("bbox")
    if not isinstance(box, list) or len(box) != 4:
        raise ValueError("bounding box must contain four coordinates")
    if any(not isinstance(v, (int, float)) or not 0 <= v <= 1 for v in box):
        raise ValueError("bounding box coordinates must be normalized")
    if box[0] >= box[2] or box[1] >= box[3]:
        raise ValueError("bounding box has invalid geometry")
    if not ground_truth and (
        not isinstance(obj.get("confidence"), (float, int))
        or not 0 <= obj["confidence"] <= 1
    ):
        raise ValueError("prediction confidence is invalid")


def intersection_over_union(a: list, b: list) -> float:
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    area = max(0, x1 - x0) * max(0, y1 - y0)
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    return area / (area_a + area_b - area)


def evaluate(
    expected: list[dict],
    predicted: list[dict],
    *,
    min_iou: float = 0.5,
    min_confidence: float = 0.5,
) -> dict:
    """One-to-one greedy matching: never count a prediction twice."""
    for obj in predicted:
        _validate_object(obj)
    remaining = set(range(len(predicted)))
    matches = []
    for truth in expected:
        _validate_object(truth, ground_truth=True)
        candidates = [
            (intersection_over_union(truth["bbox"], predicted[i]["bbox"]), i)
            for i in remaining
            if predicted[i]["label"] == truth["label"]
            and predicted[i]["confidence"] >= min_confidence
        ]
        if candidates:
            iou, index = max(candidates)
            if iou >= min_iou:
                remaining.remove(index)
                matches.append({"label": truth["label"], "iou": round(iou, 4)})
    true_positives = len(matches)
    false_negatives = len(expected) - true_positives
    false_positives = len(predicted) - true_positives
    precision = true_positives / len(predicted) if predicted else 0.0
    recall = true_positives / len(expected) if expected else 0.0
    return {
        "tp": true_positives,
        "fp": false_positives,
        "fn": false_negatives,
        "precision": precision,
        "recall": recall,
        "matches": matches,
    }


def verify_case(case: dict, prediction: dict) -> dict:
    """Fail closed when a required object is missing or a forbidden claim is made."""
    if not isinstance(prediction, dict) or not isinstance(prediction.get("objects"), list):
        raise ValueError("recognizer must return an objects array")
    labels = {o["label"] for o in prediction["objects"]}
    forbidden = set(case.get("forbidden_labels", []))
    report = evaluate(case["objects"], prediction["objects"])
    report["forbidden_found"] = sorted(labels & forbidden)
    report["passed"] = report["fn"] == 0 and report["fp"] == 0 and not report["forbidden_found"]
    return report
