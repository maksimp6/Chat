#!/data/data/com.termux/files/usr/bin/python
"""Read-only local OCR for RDC Android screenshots. No automated taps."""
from core.env import env_snapshot

import argparse
import csv
import io
import json
from pathlib import Path
import subprocess

ENV = dict(env_snapshot(), TMPDIR="/data/data/com.termux/files/usr/tmp")
DEFAULT_IMAGE = Path.home() / ".logos-rdc/screenshots/latest.jpg"


def recognize(image: Path, minimum: float = 60.0) -> list[dict]:
    """Return recognized words and JPEG pixel coordinates with confidence."""
    result = subprocess.run(
        ["tesseract", str(image), "stdout", "-l", "rus+eng", "--psm", "11", "tsv"],
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
        env=ENV,
    )
    words = []
    for item in csv.DictReader(io.StringIO(result.stdout), delimiter="\t"):
        token = (item.get("text") or "").strip()
        try:
            confidence = float(item.get("conf", "-1"))
        except ValueError:
            continue
        if not token or confidence < minimum:
            continue
        x, y = int(item["left"]), int(item["top"])
        width, height = int(item["width"]), int(item["height"])
        words.append(
            {
                "text": token,
                "confidence": round(confidence, 1),
                "x": x + width // 2,
                "y": y + height // 2,
                "bbox": [x, y, width, height],
            }
        )
    return words


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["read", "find"])
    parser.add_argument("--image", type=Path, default=DEFAULT_IMAGE)
    parser.add_argument("--text", default="")
    parser.add_argument("--min-confidence", type=float, default=60)
    args = parser.parse_args()
    if args.command == "find" and not args.text.strip():
        parser.error("--text is required for find")
    if not 0 <= args.min_confidence <= 100:
        parser.error("--min-confidence must be 0..100")
    words = recognize(args.image, args.min_confidence)
    if args.command == "find":
        query = args.text.casefold()
        words = [word for word in words if query in word["text"].casefold()]
    print(
        json.dumps(
            {
                "image": str(args.image),
                "coordinate_space": "image_pixels",
                "matches": words,
                "count": len(words),
                "tap_executed": False,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
