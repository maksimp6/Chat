import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_memory_controls_are_server_rendered():
    html = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    assert '<button id="memory-btn"' in html
    assert 'id="memoryModal" class="modal memory-modal"' in html
    assert 'class="modal-content memory-modal-content"' in html
    assert 'src="{{ static_root }}/memory_panel.js?v={{ static_version }}" defer' in html
    assert "memory_btn.js" not in html


def test_memory_modal_has_no_inline_presentation_styles():
    html = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    modal = html.split('id="memoryModal"', 1)[1].split("</div></div>", 1)[0]
    assert not re.search(r'\sstyle="', modal)


def test_memory_panel_is_idempotent_and_owns_bindings():
    js = (ROOT / "static" / "memory_panel.js").read_text(encoding="utf-8")
    assert js.count("function bindMemoryPanelEvents()") == 1
    assert 'element.dataset.bound === "true"' in js
    assert 'replaceChildren()' in js


def test_memory_panel_uses_dispatcher_for_all_transport():
    js = (ROOT / "static" / "memory_panel.js").read_text(encoding="utf-8")
    assert "window.AliceDispatcher.request(" in js
    assert not re.search(r"(?<![A-Za-z0-9_.])fetch\s*\(", js)


def test_memory_button_has_real_binding_path():
    html = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    header = (ROOT / "static" / "header_actions.js").read_text(encoding="utf-8")
    panel = (ROOT / "static" / "memory_panel.js").read_text(encoding="utf-8")
    assert 'id="memory-btn"' in html
    assert 'id="memoryModal"' in html
    assert '#memory-btn' in header
    assert "window.openMemoryModal" in header
    assert 'window.loadMemoryData' in panel
