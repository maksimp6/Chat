from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def test_theme_presets_and_persistence_are_defined():
    core = _read("static/core.js")
    style = _read("static/style.css")
    modal = _read("static/settings/settings_modal.js")

    for theme in ("light", "dark", "dim", "high-contrast"):
        assert theme in core
        assert f'[data-theme="{theme}"]' in style

    assert 'localStorage.getItem("theme")' in core
    assert 'localStorage.setItem("theme", theme)' in core
    assert 'data-tab="tab-theme"' in modal
    assert "set-theme" in modal


def test_theme_value_is_normalized_before_dom_attribute_is_set():
    core = _read("static/core.js")
    assert "normalizeAliceTheme" in core
    assert 'Object.prototype.hasOwnProperty.call(ALICE_THEMES, value)' in core
