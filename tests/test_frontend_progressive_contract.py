from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]


def test_index_has_server_rendered_critical_shell_and_no_inline_handlers():
    html = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")

    for element_id in ("app-root", "chatbox", "msg-input", "send-btn"):
        assert f'id="{element_id}"' in html

    assert not re.search(r"<[^>]+\son(?:click|change|submit|input|keydown|keyup|load)\s*=", html, re.I)


def test_optional_eruda_bundle_is_not_a_critical_script_tag():
    html = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")

    assert 'src="{{ static_root }}/eruda.js' not in html
    assert 'src="{{ static_root }}/eruda_init.js' in html


def test_memory_panel_stays_html_first():
    html = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")

    assert 'id="memoryModal"' in html
    assert 'id="memoryCloseBtn"' in html
    assert 'id="memoryClearBtn"' in html
