"""Functional project tree API with safe, bounded filesystem traversal."""

import heapq
import os

from flask import Blueprint, jsonify

project_tree_bp = Blueprint("project_tree", __name__)
PROJECT_ROOT = os.path.realpath(
    os.environ.get(
        "LOGOS_PROJECT_ROOT",
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )
)
MAX_NODES = 2000
SKIP_NAMES = {".git", "node_modules", "__pycache__", ".venv", "venv", ".pytest_cache"}


def _entry_is_dir(entry):
    """``os.path.isdir`` for a scandir entry: the folder read already knows the kind of a
    plain entry, so only a link costs a stat, and an error reads as "not a directory"."""
    try:
        return entry.is_dir()
    except OSError:
        return False


def _tree(path, depth=0, budget=None):
    # One budget for the whole walk, so MAX_NODES bounds the response, not each level.
    if budget is None:
        budget = [MAX_NODES]
    if depth > 12 or budget[0] <= 0:
        # A folder past the budget shows no children, so it is not read at all.
        return []
    entries = []
    try:
        with os.scandir(path) as listing:
            order = [
                (not _entry_is_dir(entry), entry.name.lower(), index, entry.name)
                for index, entry in enumerate(listing)
            ]
    except OSError as exc:
        raise RuntimeError(f"Cannot read project tree: {exc}") from exc
    # Directories first, then by name. A heap puts in order only the entries the budget
    # reaches, not the whole folder.
    heapq.heapify(order)

    while order:
        if budget[0] <= 0:
            break
        name = heapq.heappop(order)[3]
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
