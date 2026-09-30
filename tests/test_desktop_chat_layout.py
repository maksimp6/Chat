from pathlib import Path


STYLE = Path("static/style.css").read_text(encoding="utf-8")


def test_desktop_chat_layout_keeps_sidebar_persistent():
    assert "/* === DESKTOP CHAT LAYOUT === */" in STYLE
    assert "@media (min-width: 960px)" in STYLE
    assert ".alice-pro-app #sidebar" in STYLE
    assert "transform: none;" in STYLE
    assert "flex: 0 0 280px;" in STYLE


def test_desktop_chat_layout_disables_mobile_overlay_controls():
    assert ".alice-pro-app #overlay," in STYLE
    assert ".alice-pro-app #menu-btn," in STYLE
    assert ".alice-pro-app #close-sidebar-btn" in STYLE
    assert "display: none !important;" in STYLE


def test_desktop_chat_layout_caps_reading_width():
    assert ".alice-pro-app #chatbox," in STYLE
    assert ".alice-pro-app #input-area" in STYLE
    assert "width: min(100%, 1100px);" in STYLE
