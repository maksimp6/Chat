import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_header_actions_are_bound_by_local_asset():
    html = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "static" / "header_actions.js").read_text(encoding="utf-8")

    assert "header_actions.js?v={{ static_version }}" in html
    assert 'id="tools-btn"' in html
    assert "addEventListener" in script

    for element_id in (
        "tools-btn",
        "mcp-btn",
        "settings-btn",
        "file-manager-btn",
        "treasury-btn",
        "dozzle-btn",
        "departments-btn",
        "update-app-btn",
        "provider-credentials-btn",
        "upload-image-btn",
    ):
        match = re.search(
            rf'<(?:button|input)[^>]*id="{re.escape(element_id)}"[^>]*>',
            html,
        )
        assert match, f"Missing header control: {element_id}"
        assert "onclick=" not in match.group(0), (
            f"Header control still uses inline onclick: {element_id}"
        )


def test_header_action_bindings_are_idempotent():
    script = (ROOT / "static" / "header_actions.js").read_text(encoding="utf-8")

    assert 'element.dataset.bound === "true"' in script
    assert 'element.dataset.bound = "true"' in script
