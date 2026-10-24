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
        "menu-btn",
        "model-btn",
        "tools-btn",
        "ssh-runtime-btn",
        "mcp-btn",
        "system-status-btn",
        "settings-btn",
        "file-manager-btn",
        "treasury-btn",
        "dozzle-btn",
        "project-tree-btn",
        "departments-btn",
        "update-app-btn",
        "provider-credentials-btn",
        "memory-btn",
        "theme-toggle",
    }
    header_buttons = [button for button in parser.buttons if button.get("id") in header_ids]
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
        r"(?m)^[^{}]*#(?:menu-btn|system-status-btn|model-btn|tools-btn|ssh-runtime-btn|mcp-btn|"
        r"settings-btn|file-manager-btn|treasury-btn|dozzle-btn|project-tree-btn|"
        r"departments-btn|update-app-btn|provider-credentials-btn|memory-btn|theme-toggle)"
        r"[^{}]*\{[^{}]*(?:width|height|min-width|min-height|flex-basis)\s*:",
    )
    assert not forbidden_geometry.search(css), (
        "individual header buttons must not redefine shared geometry"
    )


def _static_javascript_sources():
    """Application JavaScript only; vendored libraries keep their upstream DOM contract."""
    vendor_files = {"eruda.js"}
    return sorted(path for path in (ROOT / "static").rglob("*.js") if path.name not in vendor_files)


def test_javascript_created_buttons_use_unified_contract():
    """Dynamically created buttons must obey the same contract as template buttons."""
    create_button = re.compile(
        r"""(?P<var>[A-Za-z_$][\w$]*)\s*=\s*document\.createElement\(\s*['"]button['"]\s*\)"""
    )
    class_assignment = r"""(?P=var)\.className\s*=\s*['"][^'"]*\balice-btn\b"""
    class_addition = r"""(?P=var)\.classList\.add\([^)]*['"]alice-btn['"]"""

    violations = []
    for path in _static_javascript_sources():
        source = path.read_text(encoding="utf-8")
        for match in create_button.finditer(source):
            window = source[match.start() : match.start() + 1200]
            var_name = match.group("var")
            has_class_name = re.search(
                rf"{re.escape(var_name)}\.className\s*=\s*['\"][^'\"]*\balice-btn\b",
                window,
            )
            has_class_add = re.search(
                rf"{re.escape(var_name)}\.classList\.add\([^)]*['\"]alice-btn['\"]",
                window,
            )
            if not (has_class_name or has_class_add):
                line = source.count("\n", 0, match.start()) + 1
                violations.append(f"{path.relative_to(ROOT)}:{line}: {var_name}")

    assert not violations, (
        "dynamic buttons must include the unified .alice-btn contract:\n" + "\n".join(violations)
    )


def test_javascript_button_markup_uses_unified_contract():
    """HTML strings must not provide a back door around the button contract."""
    violations = []
    button_tag = re.compile(r"<button\b(?P<attrs>[^>]*)>", re.IGNORECASE)

    for path in _static_javascript_sources():
        source = path.read_text(encoding="utf-8")
        for match in button_tag.finditer(source):
            attrs = match.group("attrs")
            class_match = re.search(r"""\bclass\s*=\s*['"](?P<classes>[^'"]*)['"]""", attrs)
            classes = set(class_match.group("classes").split()) if class_match else set()
            if "alice-btn" not in classes:
                line = source.count("\n", 0, match.start()) + 1
                violations.append(f"{path.relative_to(ROOT)}:{line}")

    assert not violations, (
        "button markup generated by JavaScript must include .alice-btn:\n" + "\n".join(violations)
    )


def test_buttons_never_use_inline_style_or_inline_event_handlers():
    """Presentation and behavior belong to CSS/JS modules, never button attributes."""
    template = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    parser = Parser()
    parser.feed(template)

    for button in parser.buttons:
        element_id = button.get("id") or "<unnamed>"
        assert "style" not in button, f"{element_id}: inline button style is forbidden"
        inline_events = sorted(name for name in button if name.lower().startswith("on"))
        assert not inline_events, (
            f"{element_id}: inline event handlers are forbidden: {inline_events}"
        )

    for path in _static_javascript_sources():
        source = path.read_text(encoding="utf-8")
        for match in re.finditer(r"<button\b(?P<attrs>[^>]*)>", source, re.IGNORECASE):
            attrs = match.group("attrs")
            line = source.count("\n", 0, match.start()) + 1
            assert not re.search(r"\bstyle\s*=", attrs, re.IGNORECASE), (
                f"{path.relative_to(ROOT)}:{line}: inline button style is forbidden"
            )
            assert not re.search(r"\bon[a-z]+\s*=", attrs, re.IGNORECASE), (
                f"{path.relative_to(ROOT)}:{line}: inline button event handler is forbidden"
            )
