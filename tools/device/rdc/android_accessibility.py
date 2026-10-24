"""Read-only Android UI hierarchy with semantic roles; local private data."""

import json
import re
import secrets
import subprocess
import xml.etree.ElementTree as ET


def read_accessibility(serial):
    """Dump XML to private shell-owned temp and clean it on every exit.

    Never use public /sdcard paths, serial is from the Live Server's pinned
    device check; no screen text is printed to logs.
    """
    if not isinstance(serial, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{5,100}", serial):
        raise RuntimeError("Untrusted accessibility device")
    temporary = "/data/local/tmp/logos-live-ui-" + secrets.token_hex(12) + ".xml"
    prefix = ["adb", "-s", serial]
    try:
        result = subprocess.run(
            prefix + ["shell", "uiautomator", "dump", temporary], capture_output=True, timeout=15
        )
        if result.returncode:
            raise RuntimeError("UI hierarchy unavailable")
        result = subprocess.run(
            prefix + ["exec-out", "cat", temporary], capture_output=True, timeout=8, check=True
        )
        if len(result.stdout) > 2_000_000:
            raise RuntimeError("UI hierarchy too large")
        root = ET.fromstring(result.stdout)
    finally:
        cleanup = subprocess.run(
            prefix + ["shell", "rm", "-f", temporary], capture_output=True, timeout=6
        )
        if cleanup.returncode:
            raise RuntimeError("UI hierarchy cleanup failed")
    nodes = []

    def walk(element, parent_id=None):
        for child in element:
            if child.tag != "node":
                walk(child, parent_id)
                continue
            a = child.attrib
            bounds = re.fullmatch(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", a.get("bounds", ""))
            if not bounds:
                walk(child, parent_id)
                continue
            x1, y1, x2, y2 = map(int, bounds.groups())
            klass = a.get("class", "")
            role = (
                "input"
                if "EditText" in klass
                else "button"
                if a.get("clickable") == "true"
                else "text"
                if "TextView" in klass
                else "container"
            )
            node_id = f"ui-{len(nodes)}"
            nodes.append(
                {
                    "id": node_id,
                    "parent_id": parent_id,
                    "resource_id": a.get("resource-id", ""),
                    "role": role,
                    "text": a.get("text", ""),
                    "description": a.get("content-desc", ""),
                    "clickable": a.get("clickable") == "true",
                    "bbox": [x1, y1, x2 - x1, y2 - y1],
                    "source": "uiautomator",
                }
            )
            walk(child, node_id)

    walk(root)
    return {"source": "uiautomator", "nodes": nodes, "count": len(nodes)}


if __name__ == "__main__":
    data = read_accessibility()
    print(
        json.dumps(
            {
                "count": data["count"],
                "roles": {
                    r: sum(n["role"] == r for n in data["nodes"])
                    for r in ("button", "input", "text", "container")
                },
            }
        )
    )
