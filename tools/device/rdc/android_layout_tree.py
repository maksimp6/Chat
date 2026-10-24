"""Infer conservative spatial and semantic relationships from OCR lines."""
import re

def build_tree(nodes):
    """Build section groups and same-row label/value links with evidence."""
    ordered = sorted(nodes, key=lambda n: (n["bbox"][1], n["bbox"][0]))
    sections = []
    relations = []
    current = None
    for node in ordered:
        text = node["text"].strip()
        x, y, w, h = node["bbox"]
        if text.endswith(":") and x < 40 and len(text) > 12:
            current = {"id": node["id"], "title": text, "children": [], "confidence": 0.75}
            sections.append(current)
            continue
        if current is not None and y > next(n["bbox"][1] for n in ordered if n["id"] == current["id"]):
            current["children"].append(node["id"])
        # Pair a left-hand label with right-hand text on the same baseline.
        if x > 150:
            left = [other for other in ordered
                    if other["id"] != node["id"]
                    and other["bbox"][0] < x
                    and abs(other["bbox"][1] - y) <= 6
                    and other["bbox"][0] + other["bbox"][2] < x
                    and other["text"].strip().endswith(":")]
            if left:
                label = max(left, key=lambda n: n["bbox"][0])
                relations.append({"type": "label_for", "from": label["id"], "to": node["id"],
                                  "confidence": 0.85, "evidence": "same_baseline_left_label"})
    # Sections end at the next heading; don't attach later sections as children.
    for i, section in enumerate(sections):
        start = next(n["bbox"][1] for n in ordered if n["id"] == section["id"])
        end = (next(n["bbox"][1] for n in ordered if n["id"] == sections[i+1]["id"])
               if i+1 < len(sections) else float("inf"))
        section["children"] = [n["id"] for n in ordered if start < n["bbox"][1] < min(end, start + 220) and n["bbox"][1] < 1600]
    return {"type": "layout-tree", "sections": sections, "relations": relations,
            "unverified": True}
