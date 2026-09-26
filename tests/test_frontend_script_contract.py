import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_RE = re.compile(r'<script[^>]+src="{{ static_root }}/([^"]+)"[^>]*>')


def test_index_references_existing_versioned_local_scripts():
    html = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    scripts = SCRIPT_RE.findall(html)
    assert scripts, "index.html should load local application scripts"

    for relative in scripts:
        path = ROOT / "static" / relative.split("?")[0]
        assert path.is_file(), f"Missing local script: {relative}"
        assert "?v={{ static_version }}" in relative, f"Local script must be versioned: {relative}"


def test_critical_boot_script_is_local_and_synchronous():
    html = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    assert '<script id="alice-boot" src="{{ static_root }}/boot.js?v={{ static_version }}"' in html
    assert " defer" not in html.split('<script id="alice-boot"', 1)[1].split("</script>", 1)[0]


def test_local_script_tags_are_deferred_or_async_except_critical_boot():
    html = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    for tag in re.findall(r'<script[^>]+src="{{ static_root }}/[^"]+"[^>]*>', html):
        if "/boot.js?" in tag:
            continue
        assert (" defer" in tag) or (" async" in tag), (
            f"Application script is neither deferred nor async: {tag}"
        )


def test_index_has_no_inline_application_script():
    html = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    assert not re.search(r"<script(?![^>]+src=)[^>]*>.*?</script>", html, re.DOTALL)


def test_critical_sidebar_controls_keep_native_html_semantics():
    html = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    sidebar = (ROOT / "static" / "sidebar.js").read_text(encoding="utf-8")

    assert '<button id="new-chat-btn" type="button"' in html
    assert '<button id="close-sidebar-btn" type="button"' in html
    assert '<button id="menu-btn" type="button"' in html
    assert 'link.href = "?conversation_id=" + encodeURIComponent(conv.id);' in sidebar
    assert "e.preventDefault();" in sidebar
    assert "window.__aliceSidebarHistoryBound" in sidebar
    assert 'document.addEventListener("DOMContentLoaded", initSidebar, { once: true });' in sidebar


def test_index_exposes_server_rendered_conversation_baseline():
    html = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    assert 'data-server-rendered="true"' in html
    assert "{{ selected_conversation.title }}" in html
    assert "{{ message.text }}" in html


def test_index_route_renders_conversation_from_query_server_side():
    app_source = (ROOT / "app.py").read_text(encoding="utf-8")
    assert 'request.args.get("conversation_id")' in app_source
    assert "get_owned_conversation(conversation_id, owner_id)" in app_source
    assert "selected_messages = get_messages(conversation_id)" in app_source
    assert "selected_conversation=selected_conversation" in app_source


def test_boot_is_first_and_project_tree_module_is_loaded():
    html = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    boot = html.index('id="alice-boot"')
    project_tree = html.index("/project_tree.js?")
    assert boot < project_tree
    assert 'id="project-tree-btn"' in html
    assert "Структура проекта" in html
