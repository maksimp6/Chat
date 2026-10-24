from pathlib import Path


def read(path):
    return Path(path).read_text(encoding="utf-8")


def test_conversations_have_real_fallback_links():
    source = read("static/sidebar.js")

    assert 'var link = document.createElement("a");' in source
    assert 'link.href = "?conversation_id=" + encodeURIComponent(conv.id);' in source
    assert 'link.addEventListener("click"' in source


def test_history_listener_is_owned_by_sidebar_initializer():
    source = read("static/sidebar.js")

    assert "function handleHistoryNavigation(event)" in source
    assert 'window.addEventListener("popstate", handleHistoryNavigation);' in source
    assert "window.addEventListener('popstate'" not in source


def test_empty_chat_state_avoids_inner_html():
    source = read("static/sidebar.js")

    assert "chatbox.innerHTML" not in source
    assert "chatbox.replaceChildren()" in source
