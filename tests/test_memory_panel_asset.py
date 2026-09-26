from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_memory_panel_logic_is_loaded_from_local_asset():
    template = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    assert "memory_panel.js?v={{ static_version }}" in template
    assert "function openMemoryModal()" not in template


def test_memory_panel_renders_facts_without_inner_html():
    script = (ROOT / "static" / "memory_panel.js").read_text(encoding="utf-8")
    assert "replaceChildren()" in script
    assert "textContent" in script
    assert ".innerHTML" not in script
    assert "onclick=" not in script
    assert "onchange=" not in script
    assert "addEventListener" in script
