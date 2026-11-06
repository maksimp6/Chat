#!/usr/bin/env python3
"""Generate one 24px SVG icon per Logos tool, from a fixed set of glyphs.

Tool names are read statically from tool_providers/*.py, the tool files of the
units listed in UNIT_TOOL_FILES, every unit module composition/tool_sources.py imports
and the bridge tools of mcp_server/tools.py (the same names the registry exposes). Each tool gets the first glyph whose keyword
appears in its name; unmatched tools get the generic glyph. Output:
static/icons/tools/<tool>.svg.

Run from the repository root: python3 scripts/make_tool_icons.py
"""

from __future__ import annotations

import ast
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "static" / "icons" / "tools"

# Glyph body for a 24x24 box; stroke colour comes from currentColor.
GLYPHS = {
    "document": '<path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4"/>',
    "folder": '<path d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2z"/>',
    "globe": '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c3 3.5 3 14.5 0 18M12 3c-3 3.5-3 14.5 0 18"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/>',
    "terminal": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 9l3 3-3 3M13 15h4"/>',
    "image": '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="2"/><path d="M21 17l-5-5-8 8"/>',
    "camera": '<path d="M4 8h3l2-3h6l2 3h3v11H4z"/><circle cx="12" cy="13" r="3.5"/>',
    "stack": '<ellipse cx="12" cy="6" rx="8" ry="3"/><path d="M4 6v6c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6"/>',
    "chip": '<rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4"/>',
    "branch": '<circle cx="6" cy="5" r="2"/><circle cx="6" cy="19" r="2"/><circle cx="18" cy="8" r="2"/><path d="M6 7v10M18 10c0 4-6 3-12 7"/>',
    "server": '<rect x="3" y="4" width="18" height="6" rx="1.5"/><rect x="3" y="14" width="18" height="6" rx="1.5"/><path d="M7 7h.01M7 17h.01"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "calendar": '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/>',
    "mail": '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 7l9 6 9-6"/>',
    "chat": '<path d="M4 5h16v11H9l-5 4z"/>',
    "speaker": '<path d="M4 9v6h4l5 4V5L8 9z"/><path d="M16 9a4 4 0 010 6"/>',
    "pin": '<path d="M12 21s-7-6.2-7-11a7 7 0 0114 0c0 4.8-7 11-7 11z"/><circle cx="12" cy="10" r="2.5"/>',
    "cloud": '<path d="M7 18h10a4 4 0 000-8 6 6 0 00-11.5 1.5A3.5 3.5 0 007 18z"/>',
    "lock": '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 018 0v3"/>',
    "coin": '<circle cx="12" cy="12" r="9"/><path d="M9 10h5a2 2 0 010 4h-3a2 2 0 000 4h5M12 7v10"/>',
    "printer": '<path d="M7 9V3h10v6M6 17H4v-6h16v6h-2"/><rect x="7" y="14" width="10" height="7"/>',
    "plug": '<path d="M9 3v5M15 3v5M7 8h10v4a5 5 0 01-10 0zM12 17v4"/>',
    "clipboard": '<rect x="6" y="4" width="12" height="17" rx="2"/><path d="M9 4V3h6v1M9 11h6M9 15h4"/>',
    "battery": '<rect x="3" y="7" width="16" height="10" rx="2"/><path d="M21 11v2M6 10v4M9 10v4"/>',
    "bulb": '<path d="M9 18h6M10 21h4M12 3a6 6 0 00-4 10.5c.8.7 1 1.5 1 2.5h6c0-1 .2-1.8 1-2.5A6 6 0 0012 3z"/>',
    "vibrate": '<rect x="8" y="3" width="8" height="18" rx="2"/><path d="M4 8v8M20 8v8M2 10v4M22 10v4"/>',
    "palette": '<path d="M12 3a9 9 0 000 18c1.5 0 2-1 2-2s-1-1.5 0-3 3-1 4-1a3 3 0 003-3c0-4.5-4-9-9-9z"/><circle cx="8" cy="10" r="1"/><circle cx="12" cy="7" r="1"/><circle cx="16" cy="10" r="1"/>',
    "person": '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0116 0"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7h.01"/>',
    "lines": '<path d="M4 6h16M4 12h10M4 18h13"/>',
    "archive": '<rect x="3" y="4" width="18" height="5" rx="1"/><path d="M5 9v10a2 2 0 002 2h10a2 2 0 002-2V9M10 13h4"/>',
    "hash": '<path d="M9 3L7 21M17 3l-2 18M4 9h17M3 15h17"/>',
    "pencil": '<path d="M4 20l4-1 11-11-3-3L5 16z"/><path d="M14 6l3 3"/>',
    "trash": '<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/>',
    "target": '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/>',
    "gear": '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9L7 7M17 17l2.1 2.1M4.9 19.1L7 17M17 7l2.1-2.1"/>',
    "scales": '<path d="M12 3v18M8 21h8M4 8h16"/><path d="M6 8l-3 7a3 3 0 006 0zM18 8l-3 7a3 3 0 006 0z"/>',
    "github": '<path d="M9 19c-5 1.5-5-2.5-7-3m14 6v-3.87a3.37 3.37 0 0 0-.94-2.61c3.14-.35 6.44-1.54 6.44-7A5.44 5.44 0 0 0 20 4.77 5.07 5.07 0 0 0 19.91 1S18.73.65 16 2.48a13.38 13.38 0 0 0-7 0C6.27.65 5.09 1 5.09 1A5.07 5.07 0 0 0 5 4.77a5.44 5.44 0 0 0-1.5 3.78c0 5.42 3.3 6.61 6.44 7A3.37 3.37 0 0 0 9 18.13V22"/>',
    "generic": '<rect x="4" y="4" width="16" height="16" rx="3"/><path d="M12 8v8M8 12h8"/>',
}

# First match wins; keywords are matched against the tool name in lower case.
RULES = [
    (("github",), "github"),
    (("ssl",), "lock"),
    (("court",), "scales"),
    (("rootagent",), "server"),
    (("settings", "config"), "gear"),
    (("clipboard",), "clipboard"),
    (("battery",), "battery"),
    (("torch", "brightness"), "bulb"),
    (("vibrat",), "vibrate"),
    (("theme",), "palette"),
    (("contacts",), "person"),
    (("outline", "ast"), "lines"),
    (("inspect", "system_info", "info"), "info"),
    (("zip", "archive", "extract"), "archive"),
    (("hash",), "hash"),
    (("patch",), "pencil"),
    (("delete", "remove"), "trash"),
    (("target",), "target"),
    (("search", "wiki", "web", "fetch", "http", "url"), "search"),
    (("browser",), "globe"),
    (("shell", "terminal", "tty", "exec", "command", "bash", "run_"), "terminal"),
    (("camera", "photo"), "camera"),
    (("image", "picture", "vision", "draw", "render"), "image"),
    (("embedding", "chip"), "chip"),
    (("memory", "cache", "vector", "knowledge", "rag", "fact", "store"), "stack"),
    (("git", "branch", "commit", "repo", "pull", "github"), "branch"),
    (("ssh", "server", "environment", "runtime", "remote", "rdc", "device"), "server"),
    (("schedule", "timer", "remind", "time"), "clock"),
    (("calendar",), "calendar"),
    (("sms", "mail", "notification"), "mail"),
    (("toast", "dialog", "conversation", "chat", "message"), "chat"),
    (("partner", "contact", "followup"), "person"),
    (("gov.", "tax", "requirement", "approval", "application"), "document"),
    (("tts", "voice", "audio", "speak", "sound", "volume", "speech"), "speaker"),
    (("location", "gps", "wifi"), "pin"),
    (
        ("budget", "cost", "money", "treasury", "billing", "price", "payment", "quota"),
        "coin",
    ),
    (("cloud", "yandex", "bucket", "compute", "provider"), "cloud"),
    (("secret", "credential", "password", "token", "lock", "key"), "lock"),
    (("3d", "print", "model", "stl"), "printer"),
    (("plugin", "skill", "mcp", "tool"), "plug"),
    (("folder", "directory", "dir", "list_files"), "folder"),
    (("status", "session", "invocation", "execution", "agents", "trace"), "info"),
    (("file", "read", "write", "doc", "pdf", "text"), "document"),
]


# One tool, one function: a category is a tool with its own icon, drawn with the
# glyph named here (the action icons above keep their keyword rules).
CATEGORY_GLYPHS = {
    "git": "branch",
    "termux": "chip",
    "system": "gear",
    "filesystem": "folder",
    "wikipedia": "search",
    "profiler": "clock",
    "theme": "palette",
    "runtime": "server",
    "government": "document",
    "partner": "person",
    "3d": "printer",
    "cloud": "cloud",
    "browser": "globe",
    "rootagent": "server",
    "tty": "terminal",
    "court": "scales",
    "ssl": "lock",
    "github": "github",
    "chatgpt_project": "chat",
    "chatgpt_conversation": "chat",
    "chatgpt_bridge": "chat",
    # Logos's memory: no keyword rule matches the name, and it is drawn like vectors and rag.
    "remember": "stack",
    # The Python tool runs code: a terminal, like run_command.
    "python": "terminal",
}

# Tool files of the other units that contribute categories through composition/tool_sources.py.
UNIT_TOOL_FILES = (
    "browser/tools.py",
    "conversations/tools.py",
    "photo_tasks/tools.py",
    "scheduler/tools.py",
    "semantic_cache/tools.py",
)


def _registry_name(target: ast.expr) -> bool:
    return isinstance(target, ast.Name) and (
        target.id.endswith("_TOOLS") or target.id == "TOOL_REGISTRY"
    )


def _tool_dict_keys(tree: ast.Module) -> set[str]:
    """String keys of every module-level ``*_TOOLS = {...}`` or ``TOOL_REGISTRY = {...}``
    dict, and of ``TOOL_REGISTRY["name"] = ...``: the names the registry exposes."""
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Subscript)
                    and _registry_name(target.value)
                    and isinstance(target.slice, ast.Constant)
                    and isinstance(target.slice.value, str)
                ):
                    names.add(target.slice.value)
        targets = (
            node.targets
            if isinstance(node, ast.Assign)
            else [node.target]
            if isinstance(node, ast.AnnAssign)
            else []
        )
        value = getattr(node, "value", None)
        if not isinstance(value, ast.Dict):
            continue
        if not any(_registry_name(t) for t in targets):
            continue
        for key in value.keys:
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                names.add(key.value)
    return names


def _source_files() -> list[pathlib.Path]:
    """The unit modules composition/tool_sources.py imports its tool dicts from."""
    path = ROOT / "composition" / "tool_sources.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    return [
        ROOT / (node.module.replace(".", "/") + ".py")
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    ]


def _bridge_names(tree: ast.Module) -> set[str]:
    """Bridge tools are tuples (name, title, description, input schema, handler)."""
    return {
        node.elts[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Tuple)
        and len(node.elts) == 5
        and isinstance(node.elts[0], ast.Constant)
        and isinstance(node.elts[0].value, str)
        and isinstance(node.elts[1], ast.Constant)
        and isinstance(node.elts[3], ast.Dict)
    }


def _category_names() -> set[str]:
    """The categories (tools) of the registry's own sources, the units' sources and the MCP server."""
    names: set[str] = set()
    for rel, variable in (
        ("tool_providers/registry.py", "_TOOL_SOURCES"),
        ("composition/tool_sources.py", "SOURCES"),
    ):
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"), rel)
        for node in tree.body:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
            if not any(isinstance(t, ast.Name) and t.id == variable for t in targets):
                continue
            value = getattr(node, "value", None)
            for entry in getattr(value, "elts", []):
                if isinstance(entry, ast.Tuple) and entry.elts and isinstance(entry.elts[0], ast.Constant) and isinstance(entry.elts[0].value, str):
                    names.add(entry.elts[0].value)
    bridge = ROOT / "mcp_server" / "tools.py"
    for node in ast.walk(ast.parse(bridge.read_text(encoding="utf-8"), str(bridge))):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "register"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            names.add(node.args[0].value)
    return names


def tool_names() -> list[str]:
    names: set[str] = set(_category_names())
    files = sorted((ROOT / "tool_providers").glob("*.py")) + [
        ROOT / rel for rel in UNIT_TOOL_FILES
    ] + _source_files()
    for path in sorted(set(files)):
        if not path.exists():
            continue
        names.update(
            _tool_dict_keys(ast.parse(path.read_text(encoding="utf-8"), str(path)))
        )
    bridge = ROOT / "mcp_server" / "tools.py"
    names.update(_bridge_names(ast.parse(bridge.read_text(encoding="utf-8"), str(bridge))))
    return sorted(names)


def glyph_for(name: str) -> str:
    if name in CATEGORY_GLYPHS:
        return CATEGORY_GLYPHS[name]
    lowered = name.lower()
    for keywords, glyph in RULES:
        if any(word in lowered for word in keywords):
            return glyph
    return "generic"


def svg_for(glyph: str) -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" '
        'fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" '
        f'stroke-linejoin="round" aria-hidden="true">{GLYPHS[glyph]}</svg>\n'
    )


def main() -> int:
    names = tool_names()
    if not names:
        print("no tools found", file=sys.stderr)
        return 1
    OUT.mkdir(parents=True, exist_ok=True)
    for stale in OUT.glob("*.svg"):
        stale.unlink()
    for name in names:
        (OUT / f"{name}.svg").write_text(svg_for(glyph_for(name)), encoding="utf-8")
    print(f"tools: {len(names)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
