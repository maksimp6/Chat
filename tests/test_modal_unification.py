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
                ("button", (("id", "close-modal"),), ("&times;",), ()),
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
