from html.parser import HTMLParser
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]


class Parser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.buttons = []

    def handle_starttag(self, tag, attrs):
        if tag == "button":
            self.buttons.append(dict(attrs))


def test_unified_buttons_contract():
    template = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    parser = Parser()
    parser.feed(template)

    assert parser.buttons, "template must contain buttons"

    for button in parser.buttons:
        classes = set((button.get("class") or "").split())
        element_id = button.get("id") or "<unnamed>"

        assert "alice-btn" in classes, (
            f"{element_id}: every button must use the unified .alice-btn contract"
        )
        assert button.get("type") == "button", (
            f"{element_id}: every button must explicitly declare type=button"
        )

    header_ids = {
        "menu-btn", "model-btn", "tools-btn", "ssh-runtime-btn", "mcp-btn",
        "system-status-btn", "settings-btn", "file-manager-btn", "treasury-btn", "dozzle-btn",
        "project-tree-btn", "departments-btn", "update-app-btn",
        "provider-credentials-btn", "memory-btn", "theme-toggle",
    }
    header_buttons = [
        button for button in parser.buttons if button.get("id") in header_ids
    ]
    assert len(header_buttons) == len(header_ids)

    for button in header_buttons:
        classes = set((button.get("class") or "").split())
        assert {"header-btn", "alice-btn"} <= classes
        assert button.get("title") or button.get("aria-label"), (
            f"{button.get('id')}: header button needs an accessible label"
        )

    css = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
    assert ".alice-pro-app .alice-btn {" in css
    for declaration in (
        "box-sizing: border-box",
        "border: 1px solid transparent",
        "font: inherit",
        "cursor: pointer",
        "user-select: none",
    ):
        assert declaration in css

    assert ".alice-pro-app .alice-btn:focus-visible {" in css
    assert ".alice-pro-app .alice-btn:disabled" in css

    # Header geometry belongs to the shared contract, not individual buttons.
    forbidden_geometry = re.compile(
        r"(?m)^[^{}]*#(?:menu-btn|model-btn|tools-btn|ssh-runtime-btn|mcp-btn|"
        r"settings-btn|file-manager-btn|treasury-btn|dozzle-btn|project-tree-btn|"
        r"departments-btn|update-app-btn|provider-credentials-btn|memory-btn|theme-toggle)"
        r"[^{}]*\{[^{}]*(?:width|height|min-width|min-height|flex-basis)\s*:",
    )
    assert not forbidden_geometry.search(css), (
        "individual header buttons must not redefine shared geometry"
    )
