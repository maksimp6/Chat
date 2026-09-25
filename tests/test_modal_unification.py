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
        if tag not in {"meta", "link", "input", "br", "hr", "img", "source"}:
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
        if self.stack and data.strip():
            self.stack[-1].text.append(data.strip())


def parse_modal_html():
    html = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    parser = ModalParser()
    parser.feed(html)
    parser.close()
    assert not parser.stack, "HTML ended before all tags were closed"
    return parser.roots


def walk(node):
    yield node
    for child in node.children:
        yield from walk(child)


def find_by_id(roots, element_id):
    matches = [n for root in roots for n in walk(root) if n.attrs.get("id") == element_id]
    assert len(matches) == 1, f"expected exactly one #{element_id}, got {len(matches)}"
    return matches[0]


def child(node, tag, element_id=None):
    matches = [n for n in node.children if n.tag == tag and (element_id is None or n.attrs.get("id") == element_id)]
    assert len(matches) == 1, f"expected one direct <{tag}> child, got {len(matches)}"
    return matches[0]


def test_modal_html_is_balanced_from_opening_to_closing_tag():
    roots = parse_modal_html()
    # Parsing must consume every opening/closing tag. This is intentionally a
    # full-document structural test, not a substring/regex check.
    assert roots


def test_model_modal_is_a_complete_dom_tree():
    roots = parse_modal_html()
    modal = find_by_id(roots, "model-modal")
    assert modal.tag == "div"
    assert modal.attrs.get("class") == "modal"
    assert len(modal.children) == 1
    content = child(modal, "div")
    assert content.attrs.get("class") == "modal-content"
    assert child(content, "h3").text == ["Выбор модели"]
    assert child(content, "button", "close-modal").attrs.get("id") == "close-modal"
    assert child(content, "div", "model-list").tag == "div"


def test_memory_modal_is_a_complete_dom_tree():
    roots = parse_modal_html()
    modal = find_by_id(roots, "memoryModal")
    assert modal.tag == "div"
    assert modal.attrs.get("class") == "modal memory-modal"
    assert modal.attrs.get("hidden") is not None
    assert len(modal.children) == 1

    content = child(modal, "div")
    assert content.attrs.get("class") == "modal-content memory-modal-content"
    assert content.attrs.get("role") == "dialog"
    assert content.attrs.get("aria-modal") == "true"
    assert content.attrs.get("aria-labelledby") == "memoryModalTitle"

    close = child(content, "button", "memoryCloseBtn")
    assert close.attrs.get("type") == "button"
    assert close.attrs.get("aria-label") == "Закрыть"
    assert close.text == ["×"]

    title = child(content, "h2", "memoryModalTitle")
    assert title.text == ["Управление памятью"]

    config = child(content, "div")
    assert config.attrs.get("class") == "memory-config"
    labels = [n for n in config.children if n.tag == "label"]
    assert len(labels) == 2
    assert child(labels[0], "input", "memEnabled").attrs.get("type") == "checkbox"
    assert child(labels[1], "input", "memLimit").attrs.get("type") == "number"

    clear = child(content, "div")
    assert clear.attrs.get("class") == "memory-clear"
    assert child(clear, "button", "memoryClearBtn").attrs.get("type") == "button"

    facts_title = child(content, "h3")
    assert facts_title.attrs.get("class") == "memory-facts-title"
    assert child(facts_title, "span", "memCount").text == ["0"]
    facts = child(content, "div", "memoryFactsList")
    assert facts.attrs.get("class") == "memory-facts-list"


def test_every_modal_has_one_canonical_content_child():
    roots = parse_modal_html()
    modals = [node for root in roots for node in walk(root) if "modal" in node.attrs.get("class", "").split()]
    assert modals, "no modal roots found"
    for modal in modals:
        content = [n for n in modal.children if n.tag == "div" and "modal-content" in n.attrs.get("class", "").split()]
        assert len(content) == 1, f"{modal.attrs.get('id')}: modal must contain exactly one .modal-content root"
        assert content[0].attrs.get("class", "").split()[0] == "modal-content"


def test_each_modal_dom_node_has_no_orphan_sibling_after_content():
    roots = parse_modal_html()
    modals = [node for root in roots for node in walk(root) if "modal" in node.attrs.get("class", "").split()]
    for modal in modals:
        content = [n for n in modal.children if n.tag == "div" and "modal-content" in n.attrs.get("class", "").split()][0]
        assert modal.children[-1] is content, f"{modal.attrs.get('id')}: content must run to the modal's closing tag"
