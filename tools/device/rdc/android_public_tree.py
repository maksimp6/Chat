"""Privacy-first Android UI tree projection for remote clients."""
from collections import Counter

def public_tree(hierarchy):
    """Expose structure, geometry, and roles without app text or identifiers."""
    raw = hierarchy.get("nodes", [])
    ids = {node["id"]: index for index, node in enumerate(raw)}
    nodes = []
    for index, node in enumerate(raw):
        parent = node.get("parent_id")
        nodes.append({
            "id": f"n{index}",
            "parent_id": f"n{ids[parent]}" if parent in ids else None,
            "role": node.get("role", "container"),
            "clickable": bool(node.get("clickable")),
            "bbox": node.get("bbox", []),
            "source": "accessibility",
            "text_redacted": True,
        })
    return {"type": "screen-tree", "nodes": nodes, "count": len(nodes),
            "roles": dict(Counter(node["role"] for node in nodes)),
            "read_only": True, "redacted": True}
