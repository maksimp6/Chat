from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class Node:
    def __init__(self, tag, attrs):
        self.tag = tag
        self.attrs = dict(attrs)
        self.children = []
        self.text = []
        self.raw_data = []


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
        if self.stack:
            self.stack[-1].raw_data.append(data)
            text = " ".join(data.split())
            if text:
                self.stack[-1].text.append(text)




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
        assert_enum(
            value,
            f"<{tag}>.{name}",
            {"button", "checkbox", "number", "text", "email", "password", "hidden", "submit"},
        )
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


































def _looks_like_random_gibberish(token):
    import re

    token = token.strip().lower()
    if len(token) < 5 or len(token) > 24 or not re.fullmatch(r"[a-z]+", token):
        return False
    vowels = sum(ch in "aeiouy" for ch in token)
    consonants = len(token) - vowels
    if vowels == 0 or consonants == 0:
        return False
    if re.search(r"[bcdfghjklmnpqrstvwxz]{4,}", token):
        return True
    if re.search(r"(.)\\1{2,}", token):
        return True
    # Random-looking short Latin tokens with unusually high consonant density.
    return len(token) >= 7 and consonants / len(token) >= 0.72


def test_modal_text_rejects_manual_random_gibberish():
    import re

    suspicious_fixture = "ddgdef htibv dweh"
    tokens = re.findall(r"[A-Za-z]+", suspicious_fixture)
    assert any(_looks_like_random_gibberish(token) for token in tokens), (
        "the gibberish detector must catch the manual random-text fixture"
    )












