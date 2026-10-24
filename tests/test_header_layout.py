from pathlib import Path


def test_header_actions_wrap_into_multiple_rows_without_horizontal_overflow():
    html = Path("templates/index.html").read_text(encoding="utf-8")
    css = Path("static/style.css").read_text(encoding="utf-8")
    assert 'class="header-row header-row-primary"' in html
    assert 'class="header-row header-row-secondary"' in html
    assert 'id="header-actions-2"' in html
    assert ".alice-pro-app #header {" in css
    assert "flex-direction: column;" in css
    assert ".alice-pro-app #header .header-row-secondary" in css
    assert "flex-wrap: wrap;" in css
    assert "overflow-x: visible;" in css
    assert "overflow-y: visible;" in css


def test_header_preserves_all_existing_navigation_buttons():
    html = Path("templates/index.html").read_text(encoding="utf-8")
    expected = [
        "menu-btn",
        "system-status-btn",
        "model-btn",
        "tools-btn",
        "ssh-runtime-btn",
        "mcp-btn",
        "settings-btn",
        "file-manager-btn",
        "treasury-btn",
        "dozzle-btn",
        "project-tree-btn",
        "departments-btn",
        "update-app-btn",
        "provider-credentials-btn",
        "memory-btn",
        "theme-toggle",
    ]
    for button_id in expected:
        assert 'id="' + button_id + '"' in html


def test_dynamic_header_actions_target_secondary_row():
    memory = Path("static/memory_panel.js").read_text(encoding="utf-8")
    header = Path("static/header_actions.js").read_text(encoding="utf-8")
    diagnostics = Path("static/android_diagnostics.js").read_text(encoding="utf-8")
    html = Path("templates/index.html").read_text(encoding="utf-8")
    assert 'id="memory-btn"' in html
    assert 'data-action="modal.open"' in html
    assert 'data-modal="memoryModal"' in html
    assert 'getElementById("header-actions-2")' in diagnostics
    assert "window.__aliceHeaderActionsBound === true" in header
    assert "header.memory.open" not in header
    assert "window.openMemoryModal" in memory
    assert "alice:modal:before-open" in memory


def test_ssh_runtime_panel_is_visible_on_open():
    source = Path("static/settings/ssh_runtime_modal.js").read_text(encoding="utf-8")
    assert '<div id="tab-ssh" class="llm-tab-content">' in source
    assert '<div id="tab-ssh" class="llm-tab-content" style="display:none;">' not in source


def test_memory_action_uses_document_delegation_for_dynamic_modal_lifecycle():
    source = Path("static/memory_panel.js").read_text(encoding="utf-8")
    assert "window.__aliceMemoryPanelBound === true" in source
    assert "document.addEventListener(" in source
    assert '"click"' in source
    assert 'event.target.closest("#memoryClearBtn")' in source
    assert "event.preventDefault();" in source
    assert "window.openMemoryModal();" not in source


def test_header_contract_covers_portrait_landscape_and_android_ime_constraints():
    html = Path("templates/index.html").read_text(encoding="utf-8")
    css = Path("static/style.css").read_text(encoding="utf-8")
    assert 'name="viewport"' in html
    assert "viewport-fit=cover" in html
    assert "height: 100dvh;" in css
    assert "#text-ui" in css
    assert "flex-shrink: 0;" in css
    assert "padding-bottom: calc(10px + env(safe-area-inset-bottom, 0px));" in css
    assert ".alice-pro-app #header {" in css
    assert "overflow-x: visible;" in css
    assert "overflow-y: visible;" in css
