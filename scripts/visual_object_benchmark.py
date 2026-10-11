"""Private visual E2E benchmark: pinned images -> crop -> predictor -> score -> overlay.

Default mode validates image provenance only. Live inference is opt-in and
restricted to loopback HTTP; canned responses are never reported as model E2E.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import math
import re
import statistics
import sys
from pathlib import Path
from time import perf_counter
from urllib import error, parse, request

from scripts.visual_object_contract import load_cases, verify_case

MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_PIXELS = 12_000_000
MAX_RESPONSE_BYTES = 128 * 1024
# Fixed across all cases: prompt content cannot reveal ground-truth classes.
LABEL_VOCABULARY = (
    "spool_spindle_shell",
    "retaining_ring",
    "filament_spool_holder",
    "centering_adapter",
    "phone_stand",
    "test_tube_holder",
    "sauce_stand",
    "cutting_spindle",
    "electric_motor",
    "filament_spool",
    "cnc_mill",
    "drill_press",
    "unknown",
)


def _safe_image(root: Path, case: dict) -> tuple[Path, bytes]:
    name = case["image"]
    if not re.fullmatch(r"[a-zA-Z0-9_.-]+\.png", name) or name.startswith(".") or ".." in name:
        raise ValueError("only simple PNG basenames are accepted")
    root = root.resolve(strict=True)
    path = root / name
    if path.is_symlink() or not path.is_file() or path.resolve() != root / name:
        raise ValueError("image missing, symlink, or outside root")
    if not 24 <= path.stat().st_size <= MAX_IMAGE_BYTES:
        raise ValueError("image size outside allowed bounds")
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG image")
    if "image_sha256" not in case:
        raise ValueError("case must pin source image SHA-256")
    if hashlib.sha256(data).hexdigest() != case["image_sha256"]:
        raise ValueError("image content changed: SHA-256 mismatch")
    return path, data


def _crop_image(data: bytes, crop: list[int] | None) -> tuple[bytes, tuple[int, int]]:
    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError("Pillow required for private image crops/review") from exc
    with Image.open(io.BytesIO(data)) as image:
        width, height = image.size
        if width * height > MAX_PIXELS:
            raise ValueError("image decompression bounds exceeded")
        image.load()
        if crop is not None:
            if crop[2] > width or crop[3] > height:
                raise ValueError("crop outside source image")
            image = image.crop(tuple(crop))
        image = image.convert("RGB")
        size = image.size
        output = io.BytesIO()
        image.save(output, format="PNG", optimize=False)
        return output.getvalue(), size


def _validate_endpoint(url: str) -> None:
    parts = parse.urlsplit(url)
    if (
        parts.scheme != "http"
        or parts.hostname not in {"127.0.0.1", "localhost", "::1"}
        or not parts.port
        or parts.username
        or parts.password
        or parts.fragment
        or not parts.path.startswith("/")
    ):
        raise ValueError("live predictor must be explicit loopback HTTP with port")


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("predictor redirects are forbidden")


def _request_prediction(url: str, case: dict, image: bytes) -> dict:
    _validate_endpoint(url)
    prompt = (
        "Identify visible object geometry, NOT caption text. "
        "Use canonical labels only. No guesses about motor power, CNC milling, "
        "physical installation or successful printing. "
        "Return strict JSON: objects [{label, bbox:[x0,y0,x1,y1], confidence}], "
        "claims []. Normalize bounding boxes 0..1. If no target object is "
        "visible, return objects []. Candidate labels: " + ", ".join(LABEL_VOCABULARY)
    )
    payload = json.dumps(
        {
            "case_id": hashlib.sha256(image).hexdigest()[:16],
            "image_base64": base64.b64encode(image).decode("ascii"),
            "image_sha256": hashlib.sha256(image).hexdigest(),
            "mime_type": "image/png",
            "prompt": prompt,
        },
        separators=(",", ":"),
    ).encode("utf-8")
    req = request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    opener = request.build_opener(request.ProxyHandler({}), _NoRedirect())
    with opener.open(req, timeout=15) as response:
        if response.status != 200:
            raise ValueError("predictor response status is not 200")
        raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("predictor response too large")
    result = json.loads(raw)
    if not isinstance(result, dict) or not isinstance(result.get("objects"), list):
        raise TypeError("predictor must return a strict JSON objects array")
    return result


def _private_response(predictions_dir: Path, case: dict) -> dict | None:
    path = predictions_dir / (case["id"] + ".json")
    if path.is_symlink() or not path.is_file():
        return None
    if path.stat().st_size > MAX_RESPONSE_BYTES:
        raise ValueError("prediction JSON exceeds size limit")
    return json.loads(path.read_text(encoding="utf-8"))


def _draw_objects(draw, objects: list[dict], *, offset: int, width: int, height: int, color: str):
    for obj in objects:
        x0, y0, x1, y1 = obj["bbox"]
        bounds = (
            round(x0 * width) + offset,
            round(y0 * height) + 32,
            round(x1 * width) + offset,
            round(y1 * height) + 32,
        )
        draw.rectangle(bounds, outline=color, width=3)
        draw.text(
            (bounds[0] + 3, bounds[1] + 3),
            obj["label"],
            fill=color,
            stroke_width=1,
            stroke_fill="#1b2530",
        )


def _save_overlay(
    image: bytes,
    case: dict,
    prediction: dict | None,
    path: Path,
    backend: str,
) -> None:
    from PIL import Image, ImageDraw

    with Image.open(io.BytesIO(image)) as original:
        sample = original.convert("RGB")
    width, height = sample.size
    canvas = Image.new("RGB", (width * 2, height + 32), "#17202b")
    canvas.paste(sample, (0, 32))
    canvas.paste(sample, (width, 32))
    draw = ImageDraw.Draw(canvas)
    mode = {
        "none": "MODEL NOT RUN",
        "offline_responses": "SUPPLIED RESPONSE (NOT MODEL)",
        "loopback": "LOOPBACK MODEL RESPONSE",
    }[backend]
    draw.text((8, 9), "EXPECTED OBJECTS", fill="#00d18a")
    draw.text((width + 8, 9), mode, fill="#ffb346")
    _draw_objects(draw, case["objects"], offset=0, width=width, height=height, color="#00b67a")
    predicted = prediction.get("objects", []) if prediction else []
    _draw_objects(draw, predicted, offset=width, width=width, height=height, color="#f59e0b")
    path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(path, format="PNG")


def _summary(cases: list[dict], results: list[dict], backend: str) -> dict:
    assessed = [entry for entry in results if entry["status"] == "evaluated"]
    counts = {key: sum(entry["metrics"][key] for entry in assessed) for key in ("tp", "fp", "fn")}
    tp, fp, fn = counts["tp"], counts["fp"], counts["fn"]
    counts["precision"] = (
        (round(tp / (tp + fp), 6) if tp + fp else float(fn == 0)) if assessed else None
    )
    counts["recall"] = (round(tp / (tp + fn), 6) if tp + fn else 1.0) if assessed else None
    if any(entry["status"] == "blocked" for entry in results):
        status = "BLOCKED"
    elif any(not entry["metrics"]["passed"] for entry in assessed):
        status = "FAILED"
    elif backend != "loopback" or any(c.get("annotation_status") != "reviewed" for c in cases):
        status = "REVIEW_PENDING"
    else:
        status = "EVALUATED"
    return {
        "status": status,
        "backend": backend,
        "case_count": len(cases),
        "evaluated": len(assessed),
        "source_kinds": {
            kind: sum(case.get("source_kind") == kind for case in cases)
            for kind in ("cad_render", "physical_photo")
        },
        "total": counts,
        "cases": results,
        "note": (
            "Local artifacts and/or model output are not independently validated. "
            "An EVALUATED result means only that the supplied prediction passed this test contract; it is not independent proof of live model identity or accuracy."
        ),
    }


def run_benchmark(
    manifest: Path,
    image_root: Path,
    output_root: Path,
    *,
    predictions_dir: Path | None = None,
    endpoint: str | None = None,
) -> dict:
    if endpoint and predictions_dir:
        raise ValueError("choose real loopback inference OR offline supplied responses")
    if endpoint:
        _validate_endpoint(endpoint)
    cases = load_cases(manifest)
    backend = "loopback" if endpoint else ("offline_responses" if predictions_dir else "none")
    results = []
    for case in cases:
        _, source = _safe_image(image_root, case)
        cropped, size = _crop_image(source, case.get("crop"))
        digest = hashlib.sha256(cropped).hexdigest()
        prediction = None
        reason = "model_not_invoked"
        began = perf_counter() if endpoint else None
        if endpoint:
            try:
                prediction = _request_prediction(endpoint, case, cropped)
            except (error.URLError, OSError, TimeoutError, ValueError, TypeError) as exc:
                reason = "prediction_unavailable_" + type(exc).__name__
        elif predictions_dir:
            prediction = _private_response(predictions_dir, case)
            reason = "prediction_file_absent" if prediction is None else ""
        entry: dict = {
            "id": case["id"],
            "source_sha256": case["image_sha256"],
            "inference_image_sha256": digest,
            "crop_size": list(size),
            "inference_ms": round((perf_counter() - began) * 1000, 2)
            if began is not None
            else None,
        }
        if prediction is None:
            entry.update(status="blocked", reason=reason)
        else:
            try:
                entry["metrics"] = verify_case(case, prediction)
                entry["status"] = "evaluated"
            except (ValueError, KeyError, TypeError) as exc:
                entry.update(status="blocked", reason="invalid_prediction_" + type(exc).__name__)
                prediction = None
        _save_overlay(
            cropped, case, prediction, output_root / "overlays" / (case["id"] + ".png"), backend
        )
        results.append(entry)
    report = _summary(cases, results, backend)
    latencies = [
        entry["inference_ms"]
        for entry in results
        if entry["status"] == "evaluated" and entry["inference_ms"] is not None
    ]
    report["loopback_timing_ms"] = (
        {
            "requests": len(latencies),
            "median": round(statistics.median(latencies), 2),
            "p95": sorted(latencies)[max(0, math.ceil(0.95 * len(latencies)) - 1)],
            "maximum": max(latencies),
        }
        if latencies
        else None
    )
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--image-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--predictions-dir", type=Path)
    group.add_argument("--endpoint", type=str)
    args = parser.parse_args()
    try:
        report = run_benchmark(
            args.manifest,
            args.image_root,
            args.output_root,
            predictions_dir=args.predictions_dir,
            endpoint=args.endpoint,
        )
    except (ValueError, RuntimeError, OSError, json.JSONDecodeError) as exc:
        print("BENCHMARK_FAILED", type(exc).__name__, str(exc), file=sys.stderr)
        return 1
    print("VISUAL_BENCHMARK", report["status"], json.dumps(report["total"]))
    return {"EVALUATED": 0, "REVIEW_PENDING": 2, "BLOCKED": 3, "FAILED": 4}[report["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
