"""Pure Screen DOM diff, no ADB or UI actions."""
def diff(previous, current):
    """Compare OCR nodes by stable ID; preserve exact evidence for changes."""
    old = {node["id"]: node for node in previous.get("nodes", [])}
    new = {node["id"]: node for node in current.get("nodes", [])}
    added = [node for key, node in new.items() if key not in old]
    removed = [node for key, node in old.items() if key not in new]
    changed = [{"before": old[key], "after": node}
               for key, node in new.items()
               if key in old and (node["text"] != old[key]["text"]
                                  or node["bbox"] != old[key]["bbox"])]
    return {"added": added, "removed": removed, "changed": changed,
            "counts": {"added": len(added), "removed": len(removed),
                       "changed": len(changed)}}
