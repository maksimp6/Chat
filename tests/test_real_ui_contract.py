from html.parser import HTMLParser
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")


class UiInventoryParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.header_depth = 0
        self.header_buttons = []
        self.modals = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        classes = set(values.get("class", "").split())
        if values.get("id") == "header":
            self.header_depth = 1
        elif self.header_depth:
            self.header_depth += 1

        if tag == "button" and self.header_depth:
            self.header_buttons.append(values)

        if "modal" in classes:
            self.modals.append(values)

    def handle_endtag(self, tag):
        if self.header_depth:
            self.header_depth -= 1


def _application_js():
    for path in sorted((ROOT / "static").rglob("*.js")):
        if path.name == "eruda.js":
            continue
        yield path, path.read_text(encoding="utf-8")


def test_real_header_buttons_use_registered_actions():
    parser = UiInventoryParser()
    parser.feed(HTML)
    assert parser.header_buttons, "header button inventory is empty"

    missing_actions = [
        button.get("id", "<no-id>")
        for button in parser.header_buttons
        if not button.get("data-action")
    ]
    assert not missing_actions, (
        "real header buttons must use data-action: " + ", ".join(missing_actions)
    )

    registered = set()
    register_re = re.compile(r'\.actions\.register\(\s*["\']([^"\']+)["\']')
    for _path, source in _application_js():
        registered.update(register_re.findall(source))

    unresolved = {
        button["id"]: button["data-action"]
        for button in parser.header_buttons
        if button.get("data-action") not in registered
    }
    assert not unresolved, "header actions without registered handlers: " + repr(unresolved)


def test_real_template_modals_use_one_semantic_shell():
    parser = UiInventoryParser()
    parser.feed(HTML)
    assert parser.modals, "modal inventory is empty"

    failures = {}
    for modal in parser.modals:
        modal_id = modal.get("id", "<no-id>")
        missing = []
        if modal.get("role") != "dialog":
            missing.append('role="dialog"')
        if modal.get("aria-modal") != "true":
            missing.append('aria-modal="true"')
        if "hidden" not in modal:
            missing.append("hidden")
        if missing:
            failures[modal_id] = missing

    assert not failures, "real modals violate semantic shell: " + repr(failures)
