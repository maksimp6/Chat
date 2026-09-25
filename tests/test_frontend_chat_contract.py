from pathlib import Path


def read(path):
    return Path(path).read_text(encoding="utf-8")


def test_chat_avoids_presentation_inline_styles():
    source = read("static/chat.js")
    assert "style.cssText" not in source
    assert "style.display" not in source
    assert 'style="' not in source


def test_chat_history_uses_safe_dom_fallbacks():
    source = read("static/chat.js")
    assert "chatbox.innerHTML" not in source
    assert "chatbox.replaceChildren()" in source
    assert 'className = "empty-state error-state"' in source


def test_chat_approval_and_metadata_use_owned_css_classes():
    source = read("static/chat.js")
    for css_class in (
        "approval-card",
        "approval-btn",
        "msg-meta",
        "msg-meta-details",
        "msg-meta-step",
        "alice-trace-open-viewer",
    ):
        assert css_class in source
