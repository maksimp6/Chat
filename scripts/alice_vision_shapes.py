"""Small on-device, supervised *shape-template* vision model for Alice CAD previews.

Actual inference uses foreground pixels and calibrated component signatures.
It is NOT a general-purpose image model or a physical-photo accuracy claim.
Dependencies: Python standard library + Pillow; no network or GPU required.
Private source images and learned signatures stay on the authorized device.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
from collections import deque
from pathlib import Path

from PIL import Image

from scripts.visual_object_benchmark import _crop_image, _safe_image
from scripts.visual_object_contract import intersection_over_union, load_cases

SCHEMA = "alice-shape-template-v1"
GRID = 12
MAX_EDGE = 512
MAX_PIXELS = 12_000_000
MAX_TEMPLATES = 48
MIN_COMPONENT_RATIO = 0.003
MAX_COMPONENTS = 20
TRUSTED_LABELS = frozenset(
    {
        "spool_spindle_shell",
        "retaining_ring",
        "filament_spool_holder",
        "centering_adapter",
        "phone_stand",
        "test_tube_holder",
        "sauce_stand",
    }
)


def _foreground(color: tuple[int, int, int]) -> bool:
    red, green, blue = color
    return max(color) - min(color) > 24 and max(color) < 225 and red + green + blue < 550


def _prepare_rgb(image_data: bytes) -> Image.Image:
    with Image.open(io.BytesIO(image_data)) as source:
        if source.width * source.height > MAX_PIXELS:
            raise ValueError("input image exceeds pixel limit")
        source.load()
        if "A" in source.getbands() or "transparency" in source.info:
            opaque = Image.new("RGBA", source.size, (255, 255, 255, 255))
            opaque.alpha_composite(source.convert("RGBA"))
            image = opaque.convert("RGB")
        else:
            image = source.convert("RGB")
    # Preserve a deterministic scale for device-side performance.
    scale = max(1, math.ceil(max(image.size) / MAX_EDGE))
    if scale > 1:
        image = image.resize(
            (max(1, image.width // scale), max(1, image.height // scale)),
            Image.Resampling.NEAREST,
        )
    return image


def _components(image_data: bytes) -> list[dict]:
    image = _prepare_rgb(image_data)
    width, height = image.size
    colors = list(image.get_flattened_data())  # bounded by MAX_EDGE^2
    flags = bytearray(1 if _foreground(rgb) else 0 for rgb in colors)
    min_area = max(24, int(width * height * MIN_COMPONENT_RATIO))
    output = []
    for start in range(width * height):
        if flags[start] != 1:
            continue
        stack = deque([start])
        flags[start] = 2
        pixels = []
        x0 = width
        y0 = height
        x1 = y1 = 0
        while stack:
            index = stack.pop()
            pixels.append(index)
            y, x = divmod(index, width)
            x0 = min(x0, x)
            x1 = max(x1, x)
            y0 = min(y0, y)
            y1 = max(y1, y)
            neighbors = (
                index - width if y else -1,
                index + width if y < height - 1 else -1,
                index - 1 if x else -1,
                index + 1 if x < width - 1 else -1,
            )
            for neighbor in neighbors:
                if neighbor >= 0 and flags[neighbor] == 1:
                    flags[neighbor] = 2
                    stack.append(neighbor)
        if len(pixels) < min_area:
            continue
        bounds = [x0, y0, x1 + 1, y1 + 1]
        descriptor = _descriptor(pixels, bounds, width, height, colors)
        output.append(
            {
                "bbox": [x0 / width, y0 / height, (x1 + 1) / width, (y1 + 1) / height],
                "area": len(pixels),
                "descriptor": descriptor,
            }
        )
    output.sort(key=lambda comp: comp["area"], reverse=True)
    if len(output) > MAX_COMPONENTS:
        raise ValueError("image contains too many foreground components")
    return output


def _descriptor(
    pixels: list[int],
    bounds: list[int],
    frame_width: int,
    frame_height: int,
    colors: list[tuple[int, int, int]],
) -> dict:
    x0, y0, x1, y1 = bounds
    bbox_width = x1 - x0
    bbox_height = y1 - y0
    counts = [0] * (GRID * GRID)
    warmth = 0
    for pixel in pixels:
        y, x = divmod(pixel, frame_width)
        gx = min(GRID - 1, ((x - x0) * GRID) // bbox_width)
        gy = min(GRID - 1, ((y - y0) * GRID) // bbox_height)
        counts[gy * GRID + gx] += 1
        r, g, b = colors[pixel]
        warmth += r > g + 10 and g > b + 10
    reference = bbox_width * bbox_height / (GRID * GRID)
    return {
        "grid": [round(min(1.0, value / reference), 3) for value in counts],
        "aspect": round(bbox_width / bbox_height, 5),
        "fill": round(len(pixels) / (bbox_width * bbox_height), 5),
        "warmth": round(warmth / len(pixels), 5),
        "frame_area": round(len(pixels) / (frame_width * frame_height), 5),
    }


def _distance(query: dict, reference: dict) -> float:
    if len(query["grid"]) != GRID * GRID or len(reference["grid"]) != GRID * GRID:
        raise ValueError("invalid template descriptor")
    mask_distance = sum(abs(a - b) for a, b in zip(query["grid"], reference["grid"])) / (
        GRID * GRID
    )
    shape_distance = min(1.0, abs(math.log(query["aspect"] / reference["aspect"])) / 2)
    fill_distance = abs(query["fill"] - reference["fill"])
    color_distance = abs(query["warmth"] - reference["warmth"])
    return (
        0.65 * mask_distance + 0.16 * shape_distance + 0.07 * fill_distance + 0.12 * color_distance
    )


def _relative_box(box: list[float], owner: list[float]) -> list[float]:
    x0, y0, x1, y1 = owner
    return [
        round((box[0] - x0) / (x1 - x0), 6),
        round((box[1] - y0) / (y1 - y0), 6),
        round((box[2] - x0) / (x1 - x0), 6),
        round((box[3] - y0) / (y1 - y0), 6),
    ]


def _absolute_box(relative: list[float], parent: list[float]) -> list[float]:
    x0, y0, x1, y1 = parent
    return [
        round(max(0, min(1, x0 + relative[0] * (x1 - x0))), 6),
        round(max(0, min(1, y0 + relative[1] * (y1 - y0))), 6),
        round(max(0, min(1, x0 + relative[2] * (x1 - x0))), 6),
        round(max(0, min(1, y0 + relative[3] * (y1 - y0))), 6),
    ]


def _assign_components(case: dict, components: list[dict]) -> list[dict]:
    expected = case["objects"]
    if not expected:
        if components:
            raise ValueError("foreground found in negative training image")
        return []
    if len(components) == 1 and len(expected) >= 1:
        # Single contiguous shape can legitimately contain multiple parts.
        # This is a learned instance-layout template, not input case-ID routing.
        return [
            {
                "descriptor": components[0]["descriptor"],
                "outputs": [
                    {
                        "label": item["label"],
                        "relative_bbox": _relative_box(item["bbox"], components[0]["bbox"]),
                    }
                    for item in expected
                ],
            }
        ]
    if len(expected) != len(components):
        raise ValueError("ambiguous training annotations or segmentation")
    templates = []
    remaining = set(range(len(components)))
    for item in expected:
        best_index = max(
            remaining,
            key=lambda index: intersection_over_union(item["bbox"], components[index]["bbox"]),
        )
        if intersection_over_union(item["bbox"], components[best_index]["bbox"]) < 0.3:
            raise ValueError("training foreground does not match ground truth")
        remaining.remove(best_index)
        templates.append(
            {
                "descriptor": components[best_index]["descriptor"],
                "outputs": [
                    {
                        "label": item["label"],
                        "relative_bbox": _relative_box(
                            item["bbox"], components[best_index]["bbox"]
                        ),
                    }
                ],
            }
        )
    return templates


def train(manifest: Path, gallery: Path) -> dict:
    """Train only on SHA-pinned authorized *CAD render* fixtures, without IO network."""
    cases = load_cases(manifest)
    templates = []
    hashes = {}
    blank_cases = 0
    for case in cases:
        if any(obj["label"] not in TRUSTED_LABELS for obj in case["objects"]):
            raise ValueError("untrusted object class in training annotations")
        if case.get("source_kind") != "cad_render":
            raise ValueError("training from physical/private photos requires a new approval")
        if case.get("annotation_status") != "reviewed":
            # Intentional: model may train on provisional labels for diagnostics,
            # but the resulting E2E benchmark must remain REVIEW_PENDING.
            pass
        _, data = _safe_image(gallery, case)
        cropped, _ = _crop_image(data, case.get("crop"))
        components = _components(cropped)
        new_templates = _assign_components(case, components)
        templates.extend(new_templates)
        if not case["objects"]:
            blank_cases += 1
        hashes[case["image"]] = case["image_sha256"]
    if not templates or len(templates) > MAX_TEMPLATES or not blank_cases:
        raise ValueError("training requires positive and negative examples")
    model = {
        "schema": SCHEMA,
        "engine": "Pillow shape descriptor 12x12 nearest-neighbor",
        "training_kind": "CAD_ONLY_IN_SAMPLE",
        "training_images_sha256": hashes,
        "templates": templates,
        "negative_cases": blank_cases,
        "note": "This narrow model is not zero-shot; evaluate on independent images.",
    }
    validate_model(model)
    return model


def _validate_descriptor(descriptor: object) -> None:
    if not isinstance(descriptor, dict):
        raise TypeError("invalid descriptor object")
    grid = descriptor.get("grid")
    if not isinstance(grid, list) or len(grid) != GRID * GRID:
        raise ValueError("invalid silhouette grid")
    for item in grid:
        if not isinstance(item, (int, float)) or not math.isfinite(item) or not 0 <= item <= 1:
            raise ValueError("invalid silhouette feature")
    for key, low, high in (
        ("aspect", 0.001, 100),
        ("fill", 0, 1),
        ("warmth", 0, 1),
        ("frame_area", 0, 1),
    ):
        value = descriptor.get(key)
        if (
            not isinstance(value, (int, float))
            or not math.isfinite(value)
            or not low <= value <= high
        ):
            raise ValueError("invalid descriptor " + key)


def validate_model(model: object) -> None:
    if not isinstance(model, dict) or model.get("schema") != SCHEMA:
        raise ValueError("invalid model version")
    templates = model.get("templates")
    if not isinstance(templates, list) or not 0 < len(templates) <= MAX_TEMPLATES:
        raise ValueError("unbounded or empty model templates")
    for template in templates:
        if not isinstance(template, dict):
            raise TypeError("invalid model template")
        _validate_descriptor(template.get("descriptor"))
        outputs = template.get("outputs")
        if not isinstance(outputs, list) or not 0 < len(outputs) <= 4:
            raise ValueError("invalid model output count")
        for out in outputs:
            if not isinstance(out, dict) or out.get("label") not in TRUSTED_LABELS:
                raise ValueError("unsupported trained object class")
            box = out.get("relative_bbox")
            if (
                not isinstance(box, list)
                or len(box) != 4
                or any(
                    not isinstance(value, (int, float))
                    or not math.isfinite(value)
                    or not -2 <= value <= 3
                    for value in box
                )
                or box[0] >= box[2]
                or box[1] >= box[3]
            ):
                raise ValueError("invalid model relative bounding box")


def predict(image_data: bytes, model: dict, *, max_distance: float = 0.15) -> dict:
    """Perception reads only pixel bytes and learned signatures, no case IDs/GT."""
    validate_model(model)
    if not 0 < max_distance < 1:
        raise ValueError("invalid model confidence threshold")
    candidates = _components(image_data)
    results = []
    for component in candidates:
        ranked = sorted(
            (
                (_distance(component["descriptor"], template["descriptor"]), i)
                for i, template in enumerate(model["templates"])
            ),
            key=lambda pair: (pair[0], pair[1]),
        )
        distance, index = ranked[0]
        if distance > max_distance:
            continue
        template = model["templates"][index]
        label = template["outputs"][0]["label"]
        competing = [d for d, j in ranked if model["templates"][j]["outputs"][0]["label"] != label]
        if competing and min(competing) - distance < 0.045:
            continue  # abstain rather than guess between near-identical classes
        confidence = round(max(0.51, 1 - distance * 1.65), 5)
        for out in template["outputs"]:
            box = _absolute_box(out["relative_bbox"], component["bbox"])
            if box[0] >= box[2] or box[1] >= box[3]:
                continue
            results.append({"label": out["label"], "bbox": box, "confidence": confidence})
    return {"objects": results, "claims": []}


def model_digest(model: dict) -> str:
    blob = json.dumps(model, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def save_model(model: dict, path: Path) -> str:
    """Local private artifact; do not commit signatures trained from private photos."""
    validate_model(model)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError("model output symlink not allowed")
    payload = json.dumps(model, indent=2, sort_keys=True) + "\n"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(payload)
    return model_digest(model)


def load_model(path: Path, *, expected_sha256: str | None = None) -> dict:
    if (
        path.is_symlink()
        or not path.is_file()
        or not 0 < path.stat().st_size <= 256_000
        or (path.stat().st_mode & 0o077) != 0
    ):
        raise ValueError("unsafe or publicly readable model artifact")
    model = json.loads(path.read_text(encoding="utf-8"))
    digest = model_digest(model)
    if expected_sha256 is not None and digest != expected_sha256:
        raise ValueError("local model fingerprint mismatch")
    validate_model(model)
    return model
