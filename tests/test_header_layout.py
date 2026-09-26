from pathlib import Path


def test_header_actions_stay_on_one_horizontal_line():
    html = Path("templates/index.html").read_text(encoding="utf-8")
    css = Path("static/style.css").read_text(encoding="utf-8")
    assert 'class="header-row header-row-primary"' in html
    assert 'class="header-row header-row-secondary"' in html
    assert 'id="header-actions-2"' in html
    assert ".alice-pro-app #header {" in css
    assert "flex-direction: row;" in css
    assert "flex-wrap: nowrap;" in css
    assert "overflow-x: auto;" in css


def test_dynamic_header_actions_target_secondary_row():
    memory = Path("static/memory_panel.js").read_text(encoding="utf-8")
    header = Path("static/header_actions.js").read_text(encoding="utf-8")
    diagnostics = Path("static/android_diagnostics.js").read_text(encoding="utf-8")
    html = Path("templates/index.html").read_text(encoding="utf-8")
    assert 'id="memory-btn"' in html
    assert 'getElementById("header-actions-2")' in diagnostics
    assert "memory-btn" in header
    assert "window.__aliceHeaderActionsBound === true" in header
    assert 'target.closest("#memory-btn")' in header
    assert "window.openMemoryModal();" in header
    assert "memory-btn" not in memory
    assert "window.openMemoryModal" in memory


def test_ssh_runtime_panel_is_visible_on_open():
    source = Path("static/settings/ssh_runtime_modal.js").read_text(encoding="utf-8")
    assert '<div id="tab-ssh" class="llm-tab-content">' in source
    assert '<div id="tab-ssh" class="llm-tab-content" style="display:none;">' not in source


def test_memory_action_uses_document_delegation_for_dynamic_modal_lifecycle():
    source = Path("static/memory_panel.js").read_text(encoding="utf-8")
    assert "window.__aliceMemoryPanelBound === true" in source
    assert 'document.addEventListener("click"' in source
    assert 'target.closest("#memoryCloseBtn, #memoryClearBtn")' in source
    assert "event.preventDefault();" in source
    assert "window.openMemoryModal();" not in source
