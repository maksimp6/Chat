"""Functional project tree API with safe, bounded filesystem traversal."""

import os
from flask import Blueprint, jsonify, request

project_tree_bp = Blueprint("project_tree", __name__)
PROJECT_ROOT = os.path.realpath(os.getenv("ALICE_PROJECT_ROOT", os.path.dirname(__file__)))
MAX_NODES = 2000
SKIP_NAMES = {".git", "node_modules", "__pycache__", ".venv", "venv", ".pytest_cache"}


def _tree(path, depth=0):
    if depth > 12:
        return []
    entries = []
    try:
        names = sorted(
            os.listdir(path),
            key=lambda value: (not os.path.isdir(os.path.join(path, value)), value.lower()),
        )
    except OSError as exc:
        raise RuntimeError(f"Cannot read project tree: {exc}") from exc

    for name in names:
        if name in SKIP_NAMES or name.startswith("."):
            continue
        full = os.path.realpath(os.path.join(path, name))
        if not (full == PROJECT_ROOT or full.startswith(PROJECT_ROOT + os.sep)):
            continue
        is_dir = os.path.isdir(full)
        item = {
            "name": name,
            "path": os.path.relpath(full, PROJECT_ROOT).replace(os.sep, "/"),
            "kind": "directory" if is_dir else "file",
            "icon": "📁" if is_dir else "📄",
        }
        if is_dir:
            item["children"] = _tree(full, depth + 1)
        entries.append(item)
        if sum(1 for _ in _walk_count(entries)) >= MAX_NODES:
            break
    return entries


def _walk_count(items):
    for item in items:
        yield item
        for child in _walk_count(item.get("children", [])):
            yield child


@project_tree_bp.route("/api/project-tree", methods=["GET"])
def get_project_tree():
    try:
        return jsonify(
            {
                "root": os.path.basename(PROJECT_ROOT) or PROJECT_ROOT,
                "path": ".",
                "nodes": _tree(PROJECT_ROOT),
            }
        )
    except RuntimeError as exc:
        return jsonify({"error": str(exc), "code": "PROJECT_TREE_ERROR"}), 500
