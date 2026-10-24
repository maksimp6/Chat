"""Guardrails for docs/development/naming.md.

Each allowlist holds known violations. New violations fail, and a fixed entry
must be removed from its allowlist, so the lists only shrink.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "templates" / "index.html"

STATIC_FILE_ALLOWLIST: set[str] = set()

TEMPLATE_ID_ALLOWLIST = {
    "header-actions-2",
    "memoryModal",
    "memoryCloseBtn",
    "memoryModalTitle",
    "memEnabled",
    "memLimit",
    "memoryClearBtn",
    "memCount",
    "memoryFactsList",
}

UI_TEXT_ALLOWLIST = {"Departments", "SSH Runtime"}

SNAKE_JS = re.compile(r"^[a-z][a-z0-9_]*\.js$")
PATCH_SUFFIX = re.compile(r"_(fix|new|old|tmp|auto|v\d+)(?=[_.])")
KEBAB_ID = re.compile(r"^[a-z][a-z0-9]*(-[a-z][a-z0-9]*)*$")
CYRILLIC = re.compile(r"[А-Яа-яЁё]")








def _static_name_ok(name):
    return bool(SNAKE_JS.match(name)) and not PATCH_SUFFIX.search(name)








def test_rules_reject_known_bad_names():
    assert not _static_name_ok("TraceViewer.js")
    assert not _static_name_ok("chat_v2.js")
    assert not _static_name_ok("header_fix.js")
    assert _static_name_ok("execution_surface.js")
    assert not KEBAB_ID.match("memoryModal")
    assert KEBAB_ID.match("model-modal")
