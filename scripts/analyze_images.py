"""Safe image diagnostics: metadata, edges, blur, color, brightness and differences.

Offline-only. Never modifies input images. Pillow is the only dependency.
Usage: python scripts/analyze_images.py image.jpg [second.jpg] --output /tmp/analysis
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter, ImageOps, ImageStat

MAX_PIXELS = 40_000_000
Image.MAX_IMAGE_PIXELS = MAX_PIXELS


def analyze(path: Path, out: Path) -> dict:
    """Inspect one image and save visual diagnostic overlays."""
    with Image.open(path) as source:
        source.load()
        original_size = source.size
        orientation = source.getexif().get(274)
        image = ImageOps.exif_transpose(source).convert("RGB")
    image.thumbnail((1600, 1600))
    gray = ImageOps.grayscale(image)
    edges = gray.filter(ImageFilter.FIND_EDGES)
    edges.save(out / (path.stem + "_edges.png"))
    ImageOps.autocontrast(image).save(out / (path.stem + "_contrast.png"))
    ImageOps.equalize(gray).save(out / (path.stem + "_equalized.png"))
    ImageOps.colorize(edges, "black", "red").save(
        out / (path.stem + "_edge_heatmap.png")
    )
    stat = ImageStat.Stat(gray)
    rgb = ImageStat.Stat(image)
    return {
        "file": path.name,
        "original_size": list(original_size),
        "orientation_exif": orientation,
        "brightness_mean": round(stat.mean[0], 3),
        "brightness_stddev": round(stat.stddev[0], 3),
        "rgb_mean": [round(v, 3) for v in rgb.mean],
        "edge_mean": round(ImageStat.Stat(edges).mean[0], 3),
        "edge_variance": round(ImageStat.Stat(edges).var[0], 3),
        "note": "Image statistics do not prove a physical print defect.",
    }


def compare(first: Path, second: Path, out: Path) -> dict:
    """Pixel differences are valid only after comparable alignment."""
    with Image.open(first) as a, Image.open(second) as b:
        a = ImageOps.exif_transpose(a).convert("RGB")
        b = ImageOps.exif_transpose(b).convert("RGB")
        a.thumbnail((1200, 1200))
        b = b.resize(a.size)
        diff = ImageChops.difference(a, b)
        ImageOps.autocontrast(diff).save(out / "difference.png")
        return {
            "mean_absolute_rgb_difference": [
                round(v, 3) for v in ImageStat.Stat(diff).mean
            ],
            "warning": "Second image was resized, not geometrically registered. "
            "Camera angle, lighting and crop may dominate differences.",
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("images", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if len(args.images) > 2:
        parser.error("At most two images supported")
    for path in args.images:
        if not path.is_file():
            parser.error(f"Missing input: {path}")
    args.output.mkdir(parents=True, exist_ok=True)
    result = {"images": [analyze(p, args.output) for p in args.images]}
    if len(args.images) == 2:
        result["comparison"] = compare(*args.images, args.output)
    (args.output / "report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
