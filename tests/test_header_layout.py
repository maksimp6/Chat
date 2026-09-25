from pathlib import Path


def test_header_has_two_explicit_action_rows():
    html = Path("templates/index.html").read_text(encoding="utf-8")
    assert 'id="header-actions-2"' in html
    assert 'class="header-row header-row-primary"' in html
    assert 'class="header-row header-row-secondary"' in html
    assert html.index('id="mcp-btn"') > html.index('id="header-actions-2"')


def test_dynamic_header_actions_target_secondary_row():
    memory = Path("static/memory_panel.js").read_text(encoding="utf-8")
    header = Path("static/header_actions.js").read_text(encoding="utf-8")
    diagnostics = Path("static/android_diagnostics.js").read_text(encoding="utf-8")
    html = Path("templates/index.html").read_text(encoding="utf-8")
    assert 'id="memory-btn"' in html
    assert 'getElementById("header-actions-2")' in diagnostics
    assert 'memory-btn' in memory
    assert 'window.openMemoryModal' in memory
    assert 'memory-btn' not in header
    assert 'window.openMemoryModal' in memory


def test_ssh_runtime_panel_is_visible_on_open():
    source = Path("static/settings/ssh_runtime_modal.js").read_text(encoding="utf-8")
    assert '<div id="tab-ssh" class="llm-tab-content">' in source
    assert '<div id="tab-ssh" class="llm-tab-content" style="display:none;">' not in source
