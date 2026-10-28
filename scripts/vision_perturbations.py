"""Private reproducible robustness images derived from pinned CAD overview renders.

Never commits source images or derived PNGs to GitHub. No external service IO.
The result is a *synthetic* robustness suite, not genuine unseen photography.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path

from PIL import Image, ImageEnhance, ImageFilter, ImageOps

from scripts.visual_object_benchmark import _crop_image, _safe_image
from scripts.visual_object_contract import load_cases

MODES = ("dim", "bright", "blur", "mirror", "gray", "occlude")


def _transform(img: Image.Image, mode: str) -> Image.Image:
    if mode == "dim":
        return ImageEnhance.Brightness(img).enhance(0.67)
    if mode == "bright":
        return ImageEnhance.Brightness(img).enhance(1.28)
    if mode == "blur":
        return img.filter(ImageFilter.GaussianBlur(radius=1.5))
    if mode == "mirror":
        return ImageOps.mirror(img)
    if mode == "gray":
        return ImageOps.grayscale(img).convert("RGB")
    if mode == "occlude":
        result = img.copy()
        from PIL import ImageDraw

        x0 = int(img.width * 0.39)
        y0 = int(img.height * 0.36)
        x1 = int(img.width * 0.59)
        y1 = int(img.height * 0.63)
        ImageDraw.Draw(result).rectangle((x0, y0, x1, y1), fill=(245, 246, 247))
        return result
    raise ValueError("unknown augmentation")


def generate_private_cases(manifest: Path, gallery: Path, out: Path) -> Path:
    cases = load_cases(manifest)
    if not all(case.get("source_kind") == "cad_render" for case in cases):
        raise ValueError("augmentations of private physical photos are not enabled")
    if out.exists():
        raise FileExistsError("output exists; use a new private directory")
    out.mkdir(parents=True, mode=0o700)
    derived = []
    for case in cases:
        _, data = _safe_image(gallery, case)
        cropped, _ = _crop_image(data, case.get("crop"))
        with Image.open(io.BytesIO(cropped)) as original:
            base = original.convert("RGB")
        for mode in MODES:
            rendered = _transform(base, mode)
            filename = f"{case['id']}__{mode}.png"
            destination = out / filename
            rendered.save(destination, format="PNG")
            destination.chmod(0o600)
            labels = []
            for obj in case["objects"]:
                box = obj["bbox"]
                if mode == "mirror":
                    box = [1 - box[2], box[1], 1 - box[0], box[3]]
                labels.append({"label": obj["label"], "bbox": box})
            derived.append(
                {
                    "id": f"{case['id']}__{mode}",
                    "image": filename,
                    "image_sha256": hashlib.sha256(
                        destination.read_bytes()
                    ).hexdigest(),
                    "source_kind": "cad_render",
                    "annotation_status": "provisional",
                    "augmentation": mode,
                    "origin_image_sha256": case["image_sha256"],
                    "objects": labels,
                    "forbidden_labels": case.get("forbidden_labels", []),
                    "forbidden_claims": case.get("forbidden_claims", []),
                }
            )
    target = out / "manifest.json"
    target.write_text(
        json.dumps(derived, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    target.chmod(0o600)
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--gallery", type=Path, required=True)
    parser.add_argument("--private-out", type=Path, required=True)
    args = parser.parse_args()
    output = generate_private_cases(args.manifest, args.gallery, args.private_out)
    count = len(load_cases(output))
    print("SYNTHETIC_PRIVATE_CORPUS", count, str(output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
