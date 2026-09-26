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
        "div",
        (
            ("aria-labelledby", "model-modal-title"),
            ("aria-modal", "true"),
            ("class", "modal"),
            ("hidden", None),
            ("id", "model-modal"),
            ("role", "dialog"),
        ),
        (),
        (
            (
                "div",
                (("class", "modal-content"),),
                (),
                (
                    ("h3", (("id", "model-modal-title"),), ("Выбор модели",), ()),
                    (
                        "button",
                        (
                            ("class", "alice-btn"),
                            ("data-action", "modal.close"),
                            ("data-modal", "model-modal"),
                            ("id", "close-modal"),
                            ("type", "button"),
                        ),
                        ("×",),
                        (),
                    ),
                    ("div", (("id", "model-list"),), (), ()),
                ),
            ),
        ),
    )
    assert shape(modal) == expected


def test_memory_modal_complete_dom_shape():
    modal = find(parse_html(), "memoryModal")
    expected = (
        "div",
        (
            ("aria-labelledby", "memoryModalTitle"),
            ("aria-modal", "true"),
            ("class", "modal memory-modal"),
            ("hidden", None),
            ("id", "memoryModal"),
            ("role", "dialog"),
        ),
        (),
        (
            (
                "div",
                (("class", "modal-content memory-modal-content"),),
                (),
                (
                    (
                        "button",
                        (
                            ("aria-label", "Закрыть"),
                            ("class", "memory-modal-close alice-btn"),
                            ("data-action", "modal.close"),
                            ("data-modal", "memoryModal"),
                            ("id", "memoryCloseBtn"),
                            ("type", "button"),
                        ),
                        ("×",),
                        (),
                    ),
                    (
                        "h2",
                        (("class", "memory-modal-title"), ("id", "memoryModalTitle")),
                        ("Управление памятью",),
                        (),
                    ),
                    (
                        "div",
                        (("class", "memory-config"),),
                        (),
                        (
                            (
                                "label",
                                (("class", "memory-config-option"),),
                                ("Включить память",),
                                (("input", (("id", "memEnabled"), ("type", "checkbox")), (), ()),),
                            ),
                            (
                                "label",
                                (("class", "memory-config-option"),),
                                ("Макс. фактов:",),
                                (
                                    (
                                        "input",
                                        (
                                            ("class", "memory-limit"),
                                            ("id", "memLimit"),
                                            ("max", "50"),
                                            ("min", "1"),
                                            ("type", "number"),
                                        ),
                                        (),
                                        (),
                                    ),
                                ),
                            ),
                        ),
                    ),
                    (
                        "div",
                        (("class", "memory-clear"),),
                        (),
                        (
                            (
                                "button",
                                (
                                    ("class", "memory-clear-btn alice-btn"),
                                    ("id", "memoryClearBtn"),
                                    ("type", "button"),
                                ),
                                ("Очистить всю память",),
                                (),
                            ),
                        ),
                    ),
                    (
                        "h3",
                        (("class", "memory-facts-title"),),
                        ("Факты (", ")"),
                        (("span", (("id", "memCount"),), ("0",), ()),),
                    ),
                    ("div", (("class", "memory-facts-list"), ("id", "memoryFactsList")), (), ()),
                ),
            ),
        ),
    )
    assert shape(modal) == expected


def test_every_static_modal_has_exactly_one_content_root_until_closing_tag():
    roots = parse_html()
    modals = [
        node
        for root in roots
        for node in walk(root)
        if "modal" in node.attrs.get("class", "").split()
    ]
    assert modals
    for modal in modals:
        content = [
            child
            for child in modal.children
            if child.tag == "div" and "modal-content" in child.attrs.get("class", "").split()
        ]
        assert len(content) == 1, f"{modal.attrs.get('id')}: expected exactly one .modal-content"
        assert modal.children[-1] is content[0], (
            f"{modal.attrs.get('id')}: .modal-content must be the final subtree before </div>"
        )


def test_no_static_modal_contains_another_modal_root():
    roots = parse_html()
    modals = [
        node
        for root in roots
        for node in walk(root)
        if "modal" in node.attrs.get("class", "").split()
    ]
    for modal in modals:
        nested = [
            node for node in list(walk(modal))[1:] if "modal" in node.attrs.get("class", "").split()
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


def test_every_modal_attribute_has_valid_type_length_enum_or_url():
    roots = parse_html()
    modals = [
        node
        for root in roots
        for node in walk(root)
        if "modal" in node.attrs.get("class", "").split()
    ]
    for modal in modals:
        for node in walk(modal):
            for name, value in node.attrs.items():
                validate_attribute(node, name, value)


def test_modal_form_controls_have_semantically_valid_attributes():
    roots = parse_html()
    controls = [
        node
        for root in roots
        for modal in [n for n in walk(root) if "modal" in n.attrs.get("class", "").split()]
        for node in walk(modal)
        if node.tag in {"input", "button", "select", "textarea", "a"}
    ]
    for node in controls:
        if node.tag == "input":
            assert "type" in node.attrs, f"<input id={node.attrs.get('id')!r}> must declare type"
        if node.tag == "button":
            assert_enum(
                node.attrs.get("type", "submit"),
                f"<button id={node.attrs.get('id')!r}>.type",
                {"button", "submit", "reset"},
            )
        if node.tag == "a" and "href" in node.attrs:
            assert_url(node.attrs["href"], f"<a id={node.attrs.get('id')!r}>.href")


def test_modal_links_are_valid_and_non_empty():
    roots = parse_html()
    for root in roots:
        for modal in [
            node for node in walk(root) if "modal" in node.attrs.get("class", "").split()
        ]:
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
        for modal in [
            node for node in walk(root) if "modal" in node.attrs.get("class", "").split()
        ]:
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


def parse_css_rules():
    css = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
    import re

    css = re.sub(r"/\\*.*?\\*/", "", css, flags=re.S)
    rules = {}
    for selector, body in re.findall(r"([^{}]+)\\{([^{}]*)\\}", css):
        declarations = {}
        for declaration in body.split(";"):
            if ":" not in declaration:
                continue
            name, value = declaration.split(":", 1)
            declarations[name.strip()] = value.strip()
        for item in selector.split(","):
            item = item.strip()
            if item and not item.startswith("@"):
                rules.setdefault(item, declarations)
    return rules


def test_canonical_modal_css_has_complete_layout_contract():
    import re

    css = (ROOT / "static" / "style.css").read_text(encoding="utf-8")

    def rule(selector):
        match = re.search(
            rf"(?m)^\s*{re.escape(selector)}\s*\{{([^{{}}]*)\}}",
            css,
        )
        assert match, f"missing CSS rule: {selector}"
        declarations = {}
        for declaration in match.group(1).split(";"):
            if ":" not in declaration:
                continue
            name, value = declaration.split(":", 1)
            declarations[name.strip()] = value.strip()
        return declarations

    root = rule(".alice-pro-app .modal")
    visible = rule(".alice-pro-app .modal.visible")
    content = rule(".alice-pro-app .modal-content")

    assert root["display"] == "none"
    assert root["position"] == "fixed"
    assert root["inset"] == "0"
    assert root["z-index"].isdigit()
    assert root["align-items"] == "center"
    assert root["justify-content"] == "center"

    assert visible["display"] == "flex"

    assert content["position"] == "relative"
    assert content["width"] == "90%"
    assert content["max-width"] == "500px"
    assert content["max-height"] == "80vh"
    assert content["overflow-y"] == "auto"


def test_feature_modal_roots_cannot_override_canonical_layout():
    forbidden = {
        "display",
        "position",
        "inset",
        "top",
        "right",
        "bottom",
        "left",
        "align-items",
        "justify-content",
        "overflow",
        "overflow-x",
        "overflow-y",
    }
    selectors = {
        ".alice-pro-app .treasury-modal",
        ".alice-pro-app .memory-modal",
        ".alice-pro-app .provider-credentials-modal",
        ".alice-pro-app .cloudru-iam-modal",
        ".alice-pro-app .file-manager-modal",
        ".alice-pro-app .file-manager-add-modal",
    }
    rules = dict(parse_css_rules())
    for selector in selectors:
        declarations = rules.get(selector, {})
        overlap = forbidden.intersection(declarations)
        assert not overlap, f"{selector} overrides canonical modal layout: {sorted(overlap)}"


def test_feature_modal_content_uses_canonical_content_shell():
    rules = dict(parse_css_rules())
    content_classes = {
        ".alice-pro-app .treasury-modal-content",
        ".alice-pro-app .memory-modal-content",
        ".alice-pro-app .provider-credentials-box",
        ".alice-pro-app .cloudru-iam-box",
        ".alice-pro-app .file-manager-box",
        ".alice-pro-app .file-manager-add-box",
    }
    forbidden = {"position", "width", "max-width", "max-height", "overflow", "overflow-y"}
    for selector in content_classes:
        declarations = rules.get(selector, {})
        # Feature styles may add visual details, but cannot replace the shared geometry.
        overlap = forbidden.intersection(declarations)
        assert not overlap, f"{selector} overrides canonical content geometry: {sorted(overlap)}"


def test_modal_responsive_css_only_adjusts_shared_content_shell():
    import re

    css = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
    media = re.search(
        r"@media\s*\(max-width:\s*768px\)\s*\{\s*\.alice-pro-app \.modal-content\s*\{([^{}]*)\}",
        css,
        flags=re.S,
    )
    assert media, "responsive modal content rule is missing"
    assert re.search(r"max-width\s*:\s*95%\s*;", media.group(1))


def test_modal_inter_tag_whitespace_is_explicit_and_bounded():
    roots = parse_html()
    for root in roots:
        for modal in [
            node for node in walk(root) if "modal" in node.attrs.get("class", "").split()
        ]:
            for node in walk(modal):
                for raw in node.raw_data:
                    if raw.strip():
                        continue
                    assert "\\r" not in raw, (
                        f"{modal.attrs.get('id')}: CR line endings are forbidden"
                    )
                    assert "\\t" not in raw, (
                        f"{modal.attrs.get('id')}: tab indentation is forbidden"
                    )
                    assert raw.count("\\n") <= 2, (
                        f"{modal.attrs.get('id')}: too many blank lines between tags"
                    )
                    for line in raw.split("\\n"):
                        assert len(line) - len(line.lstrip(" ")) <= 8, (
                            f"{modal.attrs.get('id')}: indentation exceeds 8 spaces"
                        )


def test_modal_text_nodes_have_no_accidental_edge_whitespace():
    roots = parse_html()
    for root in roots:
        for modal in [
            node for node in walk(root) if "modal" in node.attrs.get("class", "").split()
        ]:
            for node in walk(modal):
                for raw in node.raw_data:
                    if raw.strip() and "\n" not in raw:
                        stripped = raw.strip()
                        assert raw in {stripped, " " + stripped, stripped + " "}, (
                            f"{modal.attrs.get('id')}: text node has accidental edge whitespace: {raw!r}"
                        )


def test_modal_spacing_is_css_owned_not_inline_style():
    roots = parse_html()
    for root in roots:
        for modal in [
            node for node in walk(root) if "modal" in node.attrs.get("class", "").split()
        ]:
            for node in walk(modal):
                assert "style" not in node.attrs, (
                    f"{modal.attrs.get('id')}: inline style is forbidden inside modal DOM"
                )


def test_html_source_is_strict_utf8_without_bom_or_surrogates():
    source = (ROOT / "templates" / "index.html").read_bytes()
    assert not source.startswith(b"\\xef\\xbb\\xbf"), "UTF-8 BOM is forbidden"
    decoded = source.decode("utf-8")
    assert decoded.encode("utf-8") == source, "index.html must round-trip as UTF-8"
    assert not any(0xD800 <= ord(ch) <= 0xDFFF for ch in decoded), (
        "UTF-16 surrogate code points are forbidden"
    )


def test_html_declares_utf8_and_has_no_conflicting_charset():
    source = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    import re

    charsets = re.findall(r"<meta\b[^>]*charset\s*=\s*[\"']?([^\"'\s/>]+)", source, flags=re.I)
    assert charsets, "document must declare a charset"
    assert all(value.lower() == "utf-8" for value in charsets), (
        f"conflicting charset declarations: {charsets!r}"
    )


def test_modal_html_entities_and_escape_sequences_are_valid():
    source = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    roots = parse_html()
    import re

    entity_re = re.compile(r"&(?:#x[0-9a-f]+|#[0-9]+|[a-z][a-z0-9]+);", re.I)
    invalid_ampersand = re.compile(r"&(?!#x[0-9a-f]+;|#[0-9]+;|[a-z][a-z0-9]+;)", re.I)
    mojibake = re.compile(r"(?:Р[\\u0400-\\u04ff]|С[\\u0400-\\u04ff]){2,}")
    assert not mojibake.search(source), "possible UTF-8/Windows-1251 mojibake detected"

    for root in roots:
        for modal in [
            node for node in walk(root) if "modal" in node.attrs.get("class", "").split()
        ]:
            for raw in modal.raw_data:
                for match in entity_re.finditer(raw):
                    value = match.group(0)
                    assert value.endswith(";"), f"unterminated HTML entity: {value!r}"
                assert not invalid_ampersand.search(raw), (
                    f"{modal.attrs.get('id')}: raw '&' must be escaped as an HTML entity"
                )

    assert "&times;" in source
    assert "×" not in source, (
        "literal multiplication sign must use the established &times; escape in HTML source"
    )


def test_modal_text_is_unicode_after_entity_decoding():
    roots = parse_html()
    for root in roots:
        for modal in [
            node for node in walk(root) if "modal" in node.attrs.get("class", "").split()
        ]:
            for node in walk(modal):
                for text in node.text:
                    assert text == text.encode("utf-8").decode("utf-8")
                    assert "\\ufffd" not in text, (
                        f"{modal.attrs.get('id')}: replacement character indicates decode loss"
                    )
                    assert "\\x00" not in text, f"{modal.attrs.get('id')}: NUL is forbidden"


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


def test_modal_text_contains_no_obvious_random_gibberish():
    roots = parse_html()
    import re

    for root in roots:
        for modal in [
            node for node in walk(root) if "modal" in node.attrs.get("class", "").split()
        ]:
            for node in walk(modal):
                for text in node.text:
                    for token in re.findall(r"[A-Za-z]+", text):
                        assert not _looks_like_random_gibberish(token), (
                            f"{modal.attrs.get('id')}: suspicious random text: {token!r}"
                        )


def test_template_resources_resolve_to_existing_local_assets():
    import re
    from urllib.parse import urlparse

    source = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    refs = re.findall(
        r'<(?:script\b[^>]*\bsrc|link\b[^>]*\bhref)=["\']([^"\']+)["\']',
        source,
        flags=re.I,
    )
    assert refs, "template must declare browser resources"
    for ref in refs:
        assert not ref.startswith(("http://", "https://", "//", "data:", "blob:")), (
            f"external/non-local resource: {ref}"
        )
        clean = ref.split("?", 1)[0]
        clean = clean.replace("{{ static_root }}", "/static").replace("{{static_root}}", "/static")
        assert clean.startswith("/static/"), f"resource is outside local static root: {ref}"
        relative = clean.removeprefix("/static/")
        path = ROOT / "static" / relative
        assert path.is_file(), f"resource does not exist: {ref} -> {path}"


def test_template_resource_types_match_local_extensions():
    import re

    source = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    refs = re.findall(
        r'<(script|link)\b([^>]*?)\b(src|href)=["\']([^"\']+)["\']', source, flags=re.I
    )
    assert refs, "template must declare typed browser resources"
    for tag, _attrs, _attr_name, ref in refs:
        clean = (
            ref.split("?", 1)[0]
            .replace("{{ static_root }}", "/static")
            .replace("{{static_root}}", "/static")
        )
        suffix = clean.rsplit(".", 1)[-1].lower() if "." in clean.rsplit("/", 1)[-1] else ""
        if tag.lower() == "script":
            assert suffix == "js", f"script must resolve to .js: {ref}"
        elif tag.lower() == "link":
            if suffix not in {"css", "svg", "ico", "png", "jpg", "jpeg", "webp"}:
                raise AssertionError(f"unexpected link resource type: {ref}")


def test_local_resource_files_are_utf8_when_text_based():
    import re

    source = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    refs = re.findall(
        r'<(?:script\b[^>]*\bsrc|link\b[^>]*\bhref)=["\']([^"\']+)["\']',
        source,
        flags=re.I,
    )
    assert refs, "template must declare UTF-8 resource files"
    for ref in refs:
        clean = (
            ref.split("?", 1)[0]
            .replace("{{ static_root }}", "/static")
            .replace("{{static_root}}", "/static")
        )
        path = ROOT / "static" / clean.removeprefix("/static/")
        if path.suffix.lower() not in {".js", ".css", ".html", ".svg"}:
            continue
        data = path.read_bytes()
        assert not data.startswith(b"\\xef\\xbb\\xbf"), f"UTF-8 BOM in resource: {path}"
        decoded = data.decode("utf-8")
        assert decoded.encode("utf-8") == data, f"resource is not stable UTF-8: {path}"


def test_dynamic_modal_code_uses_canonical_modal_api():
    """New dynamic modal roots must come from the shared UI modal factory."""
    import re

    violations = []
    for path in sorted((ROOT / "static").rglob("*.js")):
        if path.name == "core_api.js":
            continue
        source = path.read_text(encoding="utf-8")
        for match in re.finditer(
            r"""(?:className\s*=\s*['"][^'"]*\bmodal\b|classList\.add\([^)]*['"]modal['"])""",
            source,
        ):
            # Local aliases (for example `const UI = window.AliceCoreAPI.ui`) are canonical too.
            if not re.search(
                r"\b(?:window\.AliceCoreAPI\.ui|[A-Za-z_$][\w$]*)\.modal\.create\s*\(", source
            ):
                line = source.count("\n", 0, match.start()) + 1
                violations.append(f"{path.relative_to(ROOT)}:{line}")

    assert not violations, (
        "dynamic modal roots must be created through AliceCoreAPI.ui.modal.create():\n"
        + "\n".join(violations)
    )


def test_modal_runtime_contract_is_wired_into_ci():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "node tests/test_ui_modal_contract.js" in workflow
