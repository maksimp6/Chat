#!/data/data/com.termux/files/usr/bin/python
"""Find OCR text, draw visible boxes, never tap."""
import argparse
import csv
import io
import json
from pathlib import Path
import subprocess
import sys

def find_and_mark(image, query, output, min_conf):
    proc = subprocess.run(["tesseract", str(image), "stdout", "-l", "rus+eng",
                           "--psm", "11", "tsv"], capture_output=True,
                          text=True, check=True, timeout=15)
    groups = {}
    for row in csv.DictReader(io.StringIO(proc.stdout), delimiter="\t"):
        word = (row.get("text") or "").strip()
        try:
            conf = float(row.get("conf", "-1"))
        except ValueError:
            continue
        if not word or conf < min_conf:
            continue
        key = tuple(row[k] for k in ("block_num", "par_num", "line_num"))
        groups.setdefault(key, []).append({
            "text": word, "conf": conf, "x": int(row["left"]),
            "y": int(row["top"]), "w": int(row["width"]), "h": int(row["height"])
        })
    found = []
    for words in groups.values():
        words.sort(key=lambda v: v["x"])
        for i in range(len(words)):
            for j in range(i + 1, len(words) + 1):
                part = words[i:j]
                label = " ".join(w["text"] for w in part)
                if query.casefold() != label.casefold():
                    continue
                x = min(w["x"] for w in part)
                y = min(w["y"] for w in part)
                right = max(w["x"] + w["w"] for w in part)
                bottom = max(w["y"] + w["h"] for w in part)
                found.append({"text": label, "confidence": round(min(w["conf"] for w in part), 1),
                              "bbox": [x, y, right-x, bottom-y],
                              "center": [(x+right)//2, (y+bottom)//2]})
    output.parent.mkdir(parents=True, exist_ok=True)
    if found:
        filters = []
        for item in found[:15]:
            x,y,w,h = item["bbox"]
            filters.append(f"drawbox=x={x}:y={y}:w={w}:h={h}:color=yellow:t=4")
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                        "-i", str(image), "-vf", ",".join(filters),
                        "-frames:v", "1", str(output)], check=True, timeout=15)
    else:
        # Do not claim a marked image when no text matched.
        output = None
    return {"query": query, "matches": found, "count": len(found),
            "marked_image": str(output) if output else None,
            "coordinate_space": "input_image_pixels", "tap_executed": False}

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--image", type=Path, default=Path.home()/".alice-rdc/screenshots/latest.jpg")
    p.add_argument("--text", required=True)
    p.add_argument("--output", type=Path, default=Path.home()/".alice-rdc/screenshots/marked.png")
    p.add_argument("--min-confidence", type=float, default=50)
    a = p.parse_args()
    if not a.text.strip() or not 0 <= a.min_confidence <= 100:
        p.error("non-empty text and confidence 0..100 required")
    try:
        print(json.dumps(find_and_mark(a.image, a.text.strip(), a.output, a.min_confidence), ensure_ascii=False))
    except (subprocess.SubprocessError, OSError, ValueError) as e:
        print(json.dumps({"error": type(e).__name__}), file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
