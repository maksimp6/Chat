"""Conservative fusion of OCR text and Android accessibility geometry."""


def overlap(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    w = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    h = max(0, min(ay + ah, by + bh) - max(ay, by))
    return w * h / max(1, aw * ah)


def fuse(ocr, ui, image_size, device_size):
    """Keep unverified OCR text separate; only spatially associate confirmed UI nodes."""
    iw, ih = image_size
    dw, dh = device_size
    if min(iw, ih, dw, dh) <= 0:
        raise ValueError("Invalid dimensions")
    nodes = []
    for index, raw in enumerate(ui):
        item = dict(raw)
        item["id"] = "access-" + raw["id"]
        item["parent_id"] = "access-" + raw["parent_id"] if raw.get("parent_id") else None
        item["source"] = "accessibility"
        item["relation_confidence"] = None
        nodes.append(item)
    for raw in ocr:
        x, y, w, h = raw["bbox"]
        box = [round(x * dw / iw), round(y * dh / ih), round(w * dw / iw), round(h * dh / ih)]
        candidates = [(overlap(box, node["bbox"]), node) for node in nodes]
        candidates = [(score, node) for score, node in candidates if score >= 0.8]
        candidates.sort(key=lambda p: (-p[0], p[1]["bbox"][2] * p[1]["bbox"][3]))
        parent = None
        if candidates:
            # Avoid claiming an ambiguous semantic relation.
            best = candidates[0]
            if len(candidates) == 1 or best[0] - candidates[1][0] > 0.1:
                parent = best[1]["id"]
        nodes.append(
            {
                "id": "ocr-" + str(raw.get("id", len(nodes))),
                "parent_id": parent,
                "role": "text",
                "text": raw["text"],
                "bbox": box,
                "source": "ocr",
                "clickable": None,
                "relation_confidence": 0.8 if parent else None,
            }
        )
    return {
        "nodes": nodes,
        "count": len(nodes),
        "read_only": True,
        "coordinate_space": "device_logical",
        "semantics_verified": False,
    }
