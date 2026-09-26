from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def test_theme_presets_and_persistence_are_defined():
    core = _read("static/core.js")
    style = _read("static/style.css")
    modal = _read("static/settings/settings_modal.js")

    assert "light" in core
    assert ":root {" in style
    for theme in ("dark", "dim", "high-contrast"):
        assert theme in core
        assert f'[data-theme="{theme}"]' in style

    assert 'localStorage.getItem("theme")' in core
    assert 'localStorage.setItem("theme", theme)' in core
    assert 'data-tab="tab-theme"' in modal
    assert "set-theme" in modal


def test_theme_value_is_normalized_before_dom_attribute_is_set():
    core = _read("static/core.js")
    assert "normalizeAliceTheme" in core
    assert "Object.prototype.hasOwnProperty.call(ALICE_THEMES, value)" in core


def _parse_hex(value):
    value = value.lstrip("#")
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    return tuple(int(value[i : i + 2], 16) / 255 for i in (0, 2, 4))


def _relative_luminance(rgb):
    channels = []
    for channel in rgb:
        channels.append(
            channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4
        )
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def _contrast_ratio(foreground, background):
    first = _relative_luminance(_parse_hex(foreground))
    second = _relative_luminance(_parse_hex(background))
    lighter, darker = max(first, second), min(first, second)
    return (lighter + 0.05) / (darker + 0.05)


def test_theme_primary_text_pairs_meet_wcag_aa():
    style = _read("static/style.css")
    blocks = {}
    for theme in ("light", "dark", "dim", "high-contrast"):
        marker = ":root {" if theme == "light" else f'[data-theme="{theme}"] {{'
        start = style.index(marker)
        remainder = style[start + len(marker) :]
        end = remainder.index("}")
        blocks[theme] = remainder[:end]

    for theme, block in blocks.items():
        values = {}
        for line in block.splitlines():
            if ":" not in line:
                continue
            key, value = line.strip().rstrip(";").split(":", 1)
            if key.startswith("--") and value.strip().startswith("#"):
                values[key] = value.strip()
        assert _contrast_ratio(values["--text-main"], values["--bg-app"]) >= 4.5, theme
        assert _contrast_ratio(values["--text-bubble-bot"], values["--bg-bubble-bot"]) >= 4.5, theme


def test_ai_theme_tool_requires_approval_and_returns_safe_frontend_action():
    from theme_tools import set_ui_theme

    result = set_ui_theme({"theme": "dim", "reason": "меньше яркости", "current_theme": "light"})
    assert result["success"] is True
    assert result["frontend_action"] == {
        "type": "set_theme",
        "theme": "dim",
        "previous_theme": "light",
    }

    from tool_registry import registry

    metadata = registry.get_tool_meta("set_ui_theme")
    assert metadata["requires_approval"] is True
    assert metadata["read_only"] is False


def test_theme_assistant_client_supports_apply_and_rollback():
    helper = _read("static/theme_assistant.js")
    index = _read("templates/index.html")
    assert "applyThemeAssistantResult" in helper
    assert "Вернуть предыдущую тему" in helper
    assert "theme_assistant.js" in index
