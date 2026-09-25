from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_tool_settings_live_in_tools_panel_not_llm_settings():
    source = (ROOT / "static" / "settings" / "settings_modal.js").read_text(encoding="utf-8")
    tools_start = source.index("window.openToolsModal")
    settings_start = source.index("window.openSettingsModal")
    tools_section = source[tools_start:settings_start]
    settings_section = source[settings_start:]

    assert "tools-ws-en" in tools_section
    assert "tools-ci-en" in tools_section
    assert "tools-fs-en" in tools_section
    assert "set-ws-en" not in settings_section
    assert "set-ci-en" not in settings_section
    assert "set-fs-en" not in settings_section
    assert 'data-tab="tab-tools"' not in settings_section


def test_tool_settings_are_saved_with_conversation_settings():
    source = (ROOT / "static" / "settings" / "settings_modal.js").read_text(encoding="utf-8")
    tools_start = source.index("window.openToolsModal")
    settings_start = source.index("window.openSettingsModal")
    tools_section = source[tools_start:settings_start]
    assert "settings.tools_config" in tools_section
    assert "Storage.save(settings, currentConvId)" in tools_section
