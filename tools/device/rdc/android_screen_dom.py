#!/data/data/com.termux/files/usr/bin/python
"""Construct a read-only OCR screen tree from a local Android screenshot."""
import argparse
import csv
import hashlib
import io
import json
from pathlib import Path
import subprocess
import time

def build(image):
    result = subprocess.run(["tesseract", str(image), "stdout", "-l", "rus+eng",
                             "--psm", "11", "tsv"], capture_output=True, text=True,
                            check=True, timeout=20)
    groups = {}
    for word in csv.DictReader(io.StringIO(result.stdout), delimiter="\t"):
        label = (word.get("text") or "").strip()
        try:
            conf = float(word.get("conf", "-1"))
        except ValueError:
            continue
        if not label or conf < 45:
            continue
        key = tuple(word[k] for k in ("block_num", "par_num", "line_num"))
        groups.setdefault(key, []).append({
            "text": label, "confidence": conf,
            "x": int(word["left"]), "y": int(word["top"]),
            "w": int(word["width"]), "h": int(word["height"])
        })
    nodes = []
    for words in groups.values():
        words.sort(key=lambda item: item["x"])
        text = " ".join(item["text"] for item in words)
        x = min(item["x"] for item in words)
        y = min(item["y"] for item in words)
        right = max(item["x"] + item["w"] for item in words)
        bottom = max(item["y"] + item["h"] for item in words)
        node_id = hashlib.sha256(f"{text.casefold()}:{x//12}:{y//12}".encode()).hexdigest()[:12]
        nodes.append({"id": node_id, "role": "text", "text": text,
                      "bbox": [x, y, right-x, bottom-y],
                      "confidence": round(min(item["confidence"] for item in words), 1),
                      "clickable": None})
    nodes.sort(key=lambda item: (item["bbox"][1], item["bbox"][0]))
    return {"type": "screen-dom", "version": 1, "captured_at": time.time(),
            "image": str(image), "nodes": nodes, "count": len(nodes),
            "limitations": ["OCR-derived; button roles and clickability unverified"]}

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--image", type=Path, default=Path.home()/".alice-rdc/screenshots/latest.jpg")
    p.add_argument("--output", type=Path, default=Path.home()/".alice-rdc/screen-dom.json")
    a = p.parse_args()
    dom = build(a.image)
    a.output.parent.mkdir(parents=True, exist_ok=True)
    temp = a.output.with_suffix(".tmp")
    temp.write_text(json.dumps(dom, ensure_ascii=False, indent=2))
    temp.replace(a.output)
    print(json.dumps({"count": dom["count"], "output": str(a.output),
                      "nodes": dom["nodes"][:12]}, ensure_ascii=False))

if __name__ == "__main__":
    main()
