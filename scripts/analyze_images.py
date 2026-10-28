"""Safe image diagnostics: metadata, edges, color, brightness and differences.

Offline-only. Never modifies input images. Pillow is the only dependency.
Usage: python scripts/analyze_images.py image.jpg [second.jpg] --output /tmp/analysis
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import (
    Image,
    ImageChops,
    ImageFilter,
    ImageOps,
    ImageStat,
    UnidentifiedImageError,
)

MAX_PIXELS = 40_000_000
ANALYSIS_SIZE = (1600, 1600)
COMPARISON_SIZE = (1200, 1200)


def _load_rgb(
    path: Path, max_pixels: int | None = None
) -> tuple[Image.Image, tuple[int, int], int | None]:
    """Load an image with EXIF orientation and a bounded decoded pixel count."""
    pixel_limit = MAX_PIXELS if max_pixels is None else max_pixels
    with Image.open(path) as source:
        original_size = source.size
        orientation = source.getexif().get(274)
        if source.width * source.height > pixel_limit:
            raise ValueError(
                f"Image exceeds the {pixel_limit:,}-pixel limit: {path.name}"
            )
        source.load()
        image = ImageOps.exif_transpose(source).convert("RGB")
    return image, original_size, orientation


def _save_png(image: Image.Image, path: Path) -> None:
    """Create a PNG without replacing any existing file; remove partial output on failure."""
    created = False
    try:
        target = path.open("xb")
        created = True
        with target:
            image.save(target, format="PNG")
    except Exception:
        if created:
            path.unlink(missing_ok=True)
        raise


def _analysis_outputs(path: Path, out: Path) -> dict[str, Path]:
    stem = path.stem
    return {
        "edges": out / f"{stem}_edges.png",
        "contrast": out / f"{stem}_contrast.png",
        "equalized": out / f"{stem}_equalized.png",
        "edge_heatmap": out / f"{stem}_edge_heatmap.png",
    }


def _ensure_outputs_available(outputs: list[Path], inputs: list[Path]) -> None:
    input_paths = {path.resolve() for path in inputs}
    for output in outputs:
        if output.resolve() in input_paths:
            raise ValueError(f"Output would overwrite an input image: {output.name}")
        if output.exists():
            raise FileExistsError(f"Refusing to overwrite existing output: {output}")


def analyze(path: Path, out: Path) -> dict:
    """Inspect one image and save visual diagnostic overlays."""
    image, original_size, orientation = _load_rgb(path)
    image.thumbnail(ANALYSIS_SIZE)
    outputs = _analysis_outputs(path, out)
    _ensure_outputs_available(list(outputs.values()), [path])

    gray = ImageOps.grayscale(image)
    edges = gray.filter(ImageFilter.FIND_EDGES)
    _save_png(edges, outputs["edges"])
    _save_png(ImageOps.autocontrast(image), outputs["contrast"])
    _save_png(ImageOps.equalize(gray), outputs["equalized"])
    _save_png(ImageOps.colorize(edges, "black", "red"), outputs["edge_heatmap"])

    gray_stat = ImageStat.Stat(gray)
    rgb_stat = ImageStat.Stat(image)
    edge_stat = ImageStat.Stat(edges)
    return {
        "file": path.name,
        "original_size": list(original_size),
        "oriented_size": list(image.size),
        "orientation_exif": orientation,
        "brightness_mean": round(gray_stat.mean[0], 3),
        "brightness_stddev": round(gray_stat.stddev[0], 3),
        "rgb_mean": [round(value, 3) for value in rgb_stat.mean],
        "edge_mean": round(edge_stat.mean[0], 3),
        "edge_variance": round(edge_stat.var[0], 3),
        "outputs": {key: value.name for key, value in outputs.items()},
        "note": "Image statistics do not prove a physical print defect.",
    }


def compare(first: Path, second: Path, out: Path) -> dict:
    """Compare similarly shaped images after EXIF correction and bounded resizing."""
    a, _, _ = _load_rgb(first)
    b, _, _ = _load_rgb(second)
    first_oriented_size = a.size
    second_oriented_size = b.size
    if a.width * b.height != b.width * a.height:
        raise ValueError(
            "Images have different aspect ratios; pixel comparison is not valid"
        )

    a.thumbnail(COMPARISON_SIZE)
    b = b.resize(a.size, Image.Resampling.LANCZOS)
    diff = ImageChops.difference(a, b)
    output = out / "difference.png"
    _ensure_outputs_available([output], [first, second])
    _save_png(diff, output)
    return {
        "files": [first.name, second.name],
        "oriented_sizes": [list(first_oriented_size), list(second_oriented_size)],
        "comparison_size": list(a.size),
        "mean_absolute_rgb_difference": [
            round(value, 3) for value in ImageStat.Stat(diff).mean
        ],
        "output": output.name,
        "warning": (
            "The second image was resized with its aspect ratio preserved, but the images "
            "were not geometrically registered. Camera angle, lighting and crop may "
            "dominate differences."
        ),
    }


def _validate_output_plan(images: list[Path], out: Path) -> None:
    planned = [
        output for path in images for output in _analysis_outputs(path, out).values()
    ]
    if len(images) == 2:
        planned.append(out / "difference.png")
    planned.append(out / "report.json")
    if len({path.resolve() for path in planned}) != len(planned):
        raise ValueError("Input filenames produce colliding output filenames")
    _ensure_outputs_available(planned, images)


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
    try:
        args.output.mkdir(parents=True, exist_ok=True)
        _validate_output_plan(args.images, args.output)
        result: dict[str, object] = {
            "images": [analyze(path, args.output) for path in args.images]
        }
        if len(args.images) == 2:
            result["comparison"] = compare(*args.images, args.output)
        report_path = args.output / "report.json"
        report = json.dumps(result, ensure_ascii=False, indent=2)
        with report_path.open("x", encoding="utf-8") as target:
            target.write(report)
    except (OSError, ValueError, UnidentifiedImageError) as exc:
        parser.error(str(exc))
    print(report)


if __name__ == "__main__":
    main()
