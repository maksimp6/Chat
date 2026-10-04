"""Guardrails for docs/development/naming.md.

Each allowlist holds known violations. New violations fail, and a fixed entry
must be removed from its allowlist, so the lists only shrink.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "templates" / "index.html"

STATIC_FILE_ALLOWLIST = {"trace_viewer_auto.js", "trace_viewer_timing_fix.js"}

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

UI_TEXT_ALLOWLIST: set[str] = set()

SNAKE_JS = re.compile(r"^[a-z][a-z0-9_]*\.js$")
PATCH_SUFFIX = re.compile(r"_(fix|new|old|tmp|auto|v\d+)(?=[_.])")
KEBAB_ID = re.compile(r"^[a-z][a-z0-9]*(-[a-z][a-z0-9]*)*$")
CYRILLIC = re.compile(r"[А-Яа-яЁё]")


def _static_js_names():
    return sorted(path.name for path in (ROOT / "static").rglob("*.js") if path.name != "eruda.js")


def _template_ids():
    return re.findall(r'\bid="([^"{}]+)"', TEMPLATE.read_text(encoding="utf-8"))


def _template_ui_texts():
    source = TEMPLATE.read_text(encoding="utf-8")
    texts = re.findall(r'\b(?:title|aria-label|placeholder)="([^"{}]+)"', source)
    return sorted(set(texts))


def _static_name_ok(name):
    return bool(SNAKE_JS.match(name)) and not PATCH_SUFFIX.search(name)


def test_static_js_file_names():
    names = _static_js_names()
    bad = [name for name in names if not _static_name_ok(name)]
    assert sorted(set(bad) - STATIC_FILE_ALLOWLIST) == []
    assert sorted(STATIC_FILE_ALLOWLIST - set(bad)) == [], "drop fixed names from the allowlist"


def test_template_ids_are_kebab_case():
    bad = [value for value in _template_ids() if not KEBAB_ID.match(value)]
    assert sorted(set(bad) - TEMPLATE_ID_ALLOWLIST) == []
    numbered = [value for value in _template_ids() if re.search(r"-\d+$", value)]
    assert sorted(set(numbered) - TEMPLATE_ID_ALLOWLIST) == []
    stale = TEMPLATE_ID_ALLOWLIST - set(bad) - set(numbered)
    assert not stale, f"drop fixed ids from the allowlist: {sorted(stale)}"


def test_template_ui_text_is_russian():
    bad = [text for text in _template_ui_texts() if not CYRILLIC.search(text)]
    assert sorted(set(bad) - UI_TEXT_ALLOWLIST) == []
    assert sorted(UI_TEXT_ALLOWLIST - set(bad)) == [], "drop fixed texts from the allowlist"


def test_rules_reject_known_bad_names():
    assert not _static_name_ok("TraceViewer.js")
    assert not _static_name_ok("chat_v2.js")
    assert not _static_name_ok("header_fix.js")
    assert _static_name_ok("execution_surface.js")
    assert not KEBAB_ID.match("memoryModal")
    assert KEBAB_ID.match("model-modal")
