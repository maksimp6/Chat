from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class Node:
    def __init__(self, tag, attrs):
        self.tag = tag
        self.attrs = dict(attrs)
        self.children = []
        self.text = []


class ModalParser(HTMLParser):
    VOID = {"meta", "link", "input", "br", "hr", "img", "source"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.roots = []

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs)
        if self.stack:
            self.stack[-1].children.append(node)
        else:
            self.roots.append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        node = Node(tag, attrs)
        if self.stack:
            self.stack[-1].children.append(node)
        else:
            self.roots.append(node)

    def handle_endtag(self, tag):
        assert self.stack, f"unexpected closing tag </{tag}>"
        node = self.stack.pop()
        assert node.tag == tag, f"expected </{node.tag}>, got </{tag}>"

    def handle_data(self, data):
        text = " ".join(data.split())
        if self.stack and text:
            self.stack[-1].text.append(text)


def parse_html():
    parser = ModalParser()
    parser.feed((ROOT / "templates" / "index.html").read_text(encoding="utf-8"))
    parser.close()
    assert not parser.stack, "HTML ended before all tags were closed"
    return parser.roots


def walk(node):
    yield node
    for child in node.children:
        yield from walk(child)


def find(roots, element_id):
    matches = [n for root in roots for n in walk(root) if n.attrs.get("id") == element_id]
    assert len(matches) == 1, f"expected exactly one #{element_id}, got {len(matches)}"
    return matches[0]


def shape(node):
    # The complete subtree is reduced to an exact tag/attribute/text structure.
    # Therefore a missing, reordered, duplicated, or unexpected HTML tag fails.
    attrs = tuple(sorted(node.attrs.items()))
    text = tuple(node.text)
    return (node.tag, attrs, text, tuple(shape(child) for child in node.children))


def test_document_html_is_balanced_from_first_to_last_tag():
    roots = parse_html()
    assert roots


def test_model_modal_complete_dom_shape():
    modal = find(parse_html(), "model-modal")
    expected = (
        "div", (("class", "modal"), ("id", "model-modal")), (), (
            ("div", (("class", "modal-content"),), (), (
                ("h3", (), ("Выбор модели",), ()),
                ("button", (("id", "close-modal"),), ("×",), ()),
                ("div", (("id", "model-list"),), (), ()),
            )),
        ),
    )
    assert shape(modal) == expected


def test_memory_modal_complete_dom_shape():
    modal = find(parse_html(), "memoryModal")
    expected = (
        "div", (("class", "modal memory-modal"), ("hidden", None), ("id", "memoryModal")), (), (
            ("div", (
                ("aria-labelledby", "memoryModalTitle"), ("aria-modal", "true"),
                ("class", "modal-content memory-modal-content"), ("role", "dialog"),
            ), (), (
                ("button", (("aria-label", "Закрыть"), ("class", "memory-modal-close"), ("id", "memoryCloseBtn"), ("type", "button")), ("×",), ()),
                ("h2", (("class", "memory-modal-title"), ("id", "memoryModalTitle")), ("Управление памятью",), ()),
                ("div", (("class", "memory-config"),), (), (
                    ("label", (("class", "memory-config-option"),), ("Включить память",), (
                        ("input", (("id", "memEnabled"), ("type", "checkbox")), (), ()),
                    )),
                    ("label", (("class", "memory-config-option"),), ("Макс. фактов:",), (
                        ("input", (("class", "memory-limit"), ("id", "memLimit"), ("max", "50"), ("min", "1"), ("type", "number")), (), ()),
                    )),
                )),
                ("div", (("class", "memory-clear"),), (), (
                    ("button", (("class", "memory-clear-btn"), ("id", "memoryClearBtn"), ("type", "button")), ("Очистить всю память",), ()),
                )),
                ("h3", (("class", "memory-facts-title"),), ("Факты (", ")"), (
                    ("span", (("id", "memCount"),), ("0",), ()),
                )),
                ("div", (("class", "memory-facts-list"), ("id", "memoryFactsList")), (), ()),
            )),
        ),
    )
    assert shape(modal) == expected


def test_every_static_modal_has_exactly_one_content_root_until_closing_tag():
    roots = parse_html()
    modals = [node for root in roots for node in walk(root) if "modal" in node.attrs.get("class", "").split()]
    assert modals
    for modal in modals:
        content = [
            child for child in modal.children
            if child.tag == "div" and "modal-content" in child.attrs.get("class", "").split()
        ]
        assert len(content) == 1, f"{modal.attrs.get('id')}: expected exactly one .modal-content"
        assert modal.children[-1] is content[0], (
            f"{modal.attrs.get('id')}: .modal-content must be the final subtree before </div>"
        )


def test_no_static_modal_contains_another_modal_root():
    roots = parse_html()
    modals = [node for root in roots for node in walk(root) if "modal" in node.attrs.get("class", "").split()]
    for modal in modals:
        nested = [
            node for node in walk(modal)[1:]
            if "modal" in node.attrs.get("class", "").split()
        ]
        assert not nested, f"{modal.attrs.get('id')}: nested modal roots are not allowed"


def assert_string(value, name, *, min_length=1, max_length=200):
    assert isinstance(value, str), f"{name}: expected string, got {type(value).__name__}"
    assert min_length <= len(value) <= max_length, (
        f"{name}: length {len(value)} outside {min_length}..{max_length}"
    )


def assert_integer(value, name, *, minimum=None, maximum=None):
    assert isinstance(value, str) and value.strip(), f"{name}: expected integer string"
    assert value.strip().lstrip("-").isdigit(), f"{name}: expected integer, got {value!r}"
    number = int(value)
    if minimum is not None:
        assert number >= minimum, f"{name}: {number} < minimum {minimum}"
    if maximum is not None:
        assert number <= maximum, f"{name}: {number} > maximum {maximum}"


def assert_enum(value, name, allowed):
    assert value in allowed, f"{name}: {value!r} is not in {sorted(allowed)!r}"


def assert_boolean(value, name):
    assert value in {"true", "false"}, f"{name}: expected true/false, got {value!r}"


def assert_url(value, name):
    assert_string(value, name, max_length=2048)
    assert value.startswith(("/", "http://", "https://")), f"{name}: invalid URL {value!r}"
    if value.startswith(("http://", "https://")):
        from urllib.parse import urlparse
        parsed = urlparse(value)
        assert parsed.scheme in {"http", "https"} and parsed.netloc, f"{name}: invalid absolute URL"


def validate_attribute(node, name, value):
    tag = node.tag

    if name in {"id", "class", "role", "aria-label", "aria-labelledby", "name", "placeholder"}:
        assert_string(value, f"<{tag}>.{name}")
    elif name == "type":
        assert_enum(value, f"<{tag}>.{name}", {
            "button", "checkbox", "number", "text", "email", "password", "hidden", "submit"
        })
    elif name in {"min", "max", "maxlength", "minlength", "size", "tabindex"}:
        assert_integer(value, f"<{tag}>.{name}", minimum=0)
    elif name == "step":
        assert value == "any" or _is_number(value), f"<{tag}>.step: expected number or 'any'"
    elif name in {"aria-modal", "aria-hidden"}:
        assert_boolean(value, f"<{tag}>.{name}")
    elif name in {"href", "src", "action"}:
        assert_url(value, f"<{tag}>.{name}")
    elif name == "autocomplete":
        assert_string(value, f"<{tag}>.{name}", max_length=100)
    elif name == "hidden":
        assert value is None, f"<{tag}>.hidden: boolean attribute must not have a value"
    else:
        assert_string(value, f"<{tag}>.{name}", max_length=2048)


def _is_number(value):
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


def test_every_modal_attribute_has_valid_type_length_enum_or_url():
    roots = parse_html()
    modals = [node for root in roots for node in walk(root)
              if "modal" in node.attrs.get("class", "").split()]
    for modal in modals:
        for node in walk(modal):
            for name, value in node.attrs.items():
                validate_attribute(node, name, value)


def test_modal_form_controls_have_semantically_valid_attributes():
    roots = parse_html()
    controls = [
        node for root in roots for node in walk(root)
        if node.tag in {"input", "button", "select", "textarea", "a"}
    ]
    for node in controls:
        if node.tag == "input":
            assert "type" in node.attrs, f"<input id={node.attrs.get('id')!r}> must declare type"
        if node.tag == "button":
            assert_enum(node.attrs.get("type", "submit"), f"<button id={node.attrs.get('id')!r}>.type",
                        {"button", "submit", "reset"})
        if node.tag == "a" and "href" in node.attrs:
            assert_url(node.attrs["href"], f"<a id={node.attrs.get('id')!r}>.href")


def test_modal_links_are_valid_and_non_empty():
    roots = parse_html()
    for root in roots:
        for modal in [node for node in walk(root) if "modal" in node.attrs.get("class", "").split()]:
            for link in [node for node in walk(modal) if node.tag == "a"]:
                href = link.attrs.get("href")
                assert href, f"{modal.attrs.get('id')}: link must have href"
                assert_url(href, f"{modal.attrs.get('id')}: link href")
                assert any(link.text) or link.children, (
                    f"{modal.attrs.get('id')}: link must have visible text or child content"
                )


def test_modal_numeric_constraints_are_mathematically_consistent():
    roots = parse_html()
    for root in roots:
        for modal in [node for node in walk(root) if "modal" in node.attrs.get("class", "").split()]:
            for node in walk(modal):
                if node.tag not in {"input", "textarea", "select"}:
                    continue
                minimum = node.attrs.get("min")
                maximum = node.attrs.get("max")
                if minimum is not None and maximum is not None:
                    assert float(minimum) <= float(maximum), (
                        f"{modal.attrs.get('id')}: min must not exceed max"
                    )
                minimum_length = node.attrs.get("minlength")
                maximum_length = node.attrs.get("maxlength")
                if minimum_length is not None and maximum_length is not None:
                    assert int(minimum_length) <= int(maximum_length), (
                        f"{modal.attrs.get('id')}: minlength must not exceed maxlength"
                    )
