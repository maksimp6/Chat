"""Functional project tree API with safe, bounded filesystem traversal."""
from core.env import env_get

import os
from flask import Blueprint, jsonify, request

project_tree_bp = Blueprint("project_tree", __name__)
PROJECT_ROOT = os.path.realpath(env_get("LOGOS_PROJECT_ROOT", os.path.dirname(__file__)))
MAX_NODES = 2000
SKIP_NAMES = {".git", "node_modules", "__pycache__", ".venv", "venv", ".pytest_cache"}


def _tree(path, depth=0, budget=None):
    # One budget for the whole walk, so MAX_NODES bounds the response, not each level.
    if budget is None:
        budget = [MAX_NODES]
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
        if budget[0] <= 0:
            break
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
        budget[0] -= 1
        entries.append(item)
        if is_dir:
            try:
                item["children"] = _tree(full, depth + 1, budget)
            except RuntimeError:
                # One unreadable folder is marked on its own node; the rest of the tree still loads.
                item["children"] = []
                item["unreadable"] = True
    return entries


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
