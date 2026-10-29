"""A CSS selector subset for the http engine: compiler and right-to-left matcher.

Supported: type and ``*``, ``#id``, ``.class``, attribute tests (``[a]``,
``=``, ``~=``, ``|=``, ``^=``, ``$=``, ``*=``, quoted or bare values, CSS
escapes, the ``i``/``s`` flags), the combinators (descendant, ``>``, ``+``,
``~``), selector lists and the pseudo-classes :first-child, :last-child,
:only-child, :nth-child(), :nth-last-child(), :nth-of-type(),
:nth-last-of-type(), :first-of-type, :last-of-type, :only-of-type, :not(),
:is(), :where(), :empty, :root, :checked, :disabled, :enabled, :required,
:optional, :link, :any-link and :contains("text"). Matching walks the tree
iteratively with backtracking for the descendant and sibling combinators.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Iterator

from browser.dom import Document, Element, normalize_space
from browser.errors import E_SELECTOR_SYNTAX, BrowserError

logger = logging.getLogger("logos.browser.css")


class SelectorError(BrowserError):
    """A CSS selector that cannot be parsed."""


# Attribute values compared ASCII-case-insensitively, as HTML defines them.
_CASELESS_ATTRS = frozenset({"type", "method", "enctype", "rel", "lang", "dir"})
_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")
_FORM_CONTROLS = frozenset(
    {"button", "input", "select", "textarea", "optgroup", "option", "fieldset"}
)
_FIELDS = frozenset({"input", "select", "textarea"})
_NTH_PATTERN = re.compile(r"^([+-]?\d*)n([+-]\d+)?$")
_SIMPLE_PSEUDOS = frozenset(
    {
        "first-child",
        "last-child",
        "only-child",
        "first-of-type",
        "last-of-type",
        "only-of-type",
        "empty",
        "root",
        "checked",
        "disabled",
        "enabled",
        "required",
        "optional",
        "link",
        "any-link",
    }
)
_NTH_PSEUDOS = frozenset(
    {"nth-child", "nth-last-child", "nth-of-type", "nth-last-of-type"}
)
_LIST_PSEUDOS = frozenset({"not", "is", "where", "matches"})


@dataclass(frozen=True)
class AttrTest:
    name: str
    op: str  # "" (presence), =, ~=, |=, ^=, $=, *=
    value: str = ""
    caseless: bool = False


@dataclass(frozen=True)
class Pseudo:
    name: str
    nth: tuple[int, int] | None = None
    selectors: SelectorList | None = None
    text: str | None = None


@dataclass(frozen=True)
class Compound:
    tag: str | None = None  # None matches any element
    ids: tuple[str, ...] = ()
    classes: tuple[str, ...] = ()
    attrs: tuple[AttrTest, ...] = ()
    pseudos: tuple[Pseudo, ...] = ()

    def index_key(self) -> tuple[str, str] | None:
        """("tag", name) or ("attr", name) that every match must have, if any."""
        if self.tag:
            return ("tag", self.tag)
        if self.ids:
            return ("attr", "id")
        if self.classes:
            return ("attr", "class")
        if self.attrs:
            return ("attr", self.attrs[0].name)
        return None


@dataclass(frozen=True)
class Complex:
    parts: tuple[Compound, ...]
    combinators: tuple[str, ...]  # between parts: " ", ">", "+", "~"


@dataclass(eq=False)
class SelectorList:
    """A compiled selector list, indexed by the subject's tag or required attribute.

    The index is built once at construction: compiled lists are cached and
    shared between sessions, so they are never mutated afterwards.
    """

    text: str
    selectors: tuple[Complex, ...]
    _by_tag: dict[str, list[Complex]] = field(default_factory=dict, repr=False)
    _by_attr: dict[str, list[Complex]] = field(default_factory=dict, repr=False)
    _other: list[Complex] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        for selector in self.selectors:
            key = selector.parts[-1].index_key()
            if key is None:
                self._other.append(selector)
            elif key[0] == "tag":
                self._by_tag.setdefault(key[1], []).append(selector)
            else:
                self._by_attr.setdefault(key[1], []).append(selector)

    def candidates(self, element: Element) -> Iterator[Complex]:
        """The selectors whose subject could match ``element``."""
        yield from self._by_tag.get(element.tag, ())
        if self._by_attr and element.attrs:
            for name in element.attrs:
                yield from self._by_attr.get(name, ())
        yield from self._other

    def matches(self, element: Element) -> bool:
        if element.tag == "#document":
            return False
        return any(_match_complex(element, sel) for sel in self.candidates(element))


# ---------------------------------------------------------------------------
# Parser


class _Parser:
    def __init__(self, text: str) -> None:
        self.text = text
        self.pos = 0

    def error(self, reason: str, pos: int | None = None) -> SelectorError:
        where = (self.pos if pos is None else pos) + 1
        return SelectorError(
            E_SELECTOR_SYNTAX.format(sel=self.text, reason=reason, pos=where)
        )

    def peek(self) -> str:
        return self.text[self.pos] if self.pos < len(self.text) else ""

    def at_end(self) -> bool:
        return self.pos >= len(self.text)

    def skip_ws(self) -> bool:
        start = self.pos
        while self.pos < len(self.text) and self.text[self.pos] in " \t\r\n\f":
            self.pos += 1
        return self.pos > start

    def read_escape(self) -> str:
        # At a backslash: hex escape (1-6 digits, one optional space) or a literal.
        self.pos += 1
        if self.at_end():
            return "\ufffd"
        match = re.match(r"[0-9a-fA-F]{1,6}", self.text[self.pos : self.pos + 6])
        if match:
            self.pos += len(match.group(0))
            if self.peek() in (" ", "\t", "\n", "\r", "\f"):
                self.pos += 1
            code = int(match.group(0), 16)
            if code == 0 or code > 0x10FFFF or 0xD800 <= code <= 0xDFFF:
                return "\ufffd"
            return chr(code)
        char = self.text[self.pos]
        self.pos += 1
        return char

    def read_ident(self) -> str:
        out: list[str] = []
        while not self.at_end():
            char = self.text[self.pos]
            if char == "\\":
                out.append(self.read_escape())
            elif char.isalnum() or char in "-_" or ord(char) >= 0x80:
                out.append(char)
                self.pos += 1
            else:
                break
        return "".join(out)

    def read_string(self) -> str:
        quote = self.text[self.pos]
        start = self.pos
        self.pos += 1
        out: list[str] = []
        while True:
            if self.at_end():
                raise self.error("не закрыта кавычка", start)
            char = self.text[self.pos]
            if char == quote:
                self.pos += 1
                return "".join(out)
            if char == "\\":
                if self.text[self.pos + 1 : self.pos + 2] == "\n":
                    self.pos += 2
                    continue
                out.append(self.read_escape())
            else:
                out.append(char)
                self.pos += 1

    def read_raw_argument(self) -> str:
        """Text up to the matching ")" (quotes respected); the ")" is consumed."""
        start = self.pos
        depth = 0
        while not self.at_end():
            char = self.text[self.pos]
            if char in "\"'":
                self.read_string()
                continue
            if char == "\\":
                self.pos += 2
                continue
            if char == "(":
                depth += 1
            elif char == ")":
                if depth == 0:
                    value = self.text[start : self.pos]
                    self.pos += 1
                    return value
                depth -= 1
            self.pos += 1
        raise self.error("не закрыта скобка «(»", start - 1)

    def parse_list(self, *, nested: bool = False) -> tuple[Complex, ...]:
        selectors: list[Complex] = []
        self.skip_ws()
        while True:
            selectors.append(self.parse_complex())
            self.skip_ws()
            if self.at_end():
                if nested:
                    raise self.error("не закрыта скобка «(»")
                break
            char = self.peek()
            if char == ",":
                self.pos += 1
                self.skip_ws()
                continue
            if nested and char == ")":
                self.pos += 1
                break
            raise self.error(f"неожиданный символ «{char}»")
        return tuple(selectors)

    def parse_complex(self) -> Complex:
        parts = [self.parse_compound()]
        combinators: list[str] = []
        while True:
            had_space = self.skip_ws()
            if self.at_end() or self.peek() in ",)":
                break
            char = self.peek()
            if char in ">+~":
                self.pos += 1
                self.skip_ws()
                combinator = char
            elif had_space:
                combinator = " "
            else:
                raise self.error(f"неожиданный символ «{char}»")
            parts.append(self.parse_compound())
            combinators.append(combinator)
        return Complex(tuple(parts), tuple(combinators))

    def parse_compound(self) -> Compound:
        start = self.pos
        tag: str | None = None
        ids: list[str] = []
        classes: list[str] = []
        attrs: list[AttrTest] = []
        pseudos: list[Pseudo] = []
        char = self.peek()
        if char == "*":
            self.pos += 1
        elif char and (char.isalpha() or char in "_\\" or ord(char) >= 0x80):
            tag = self.read_ident().lower()
        while not self.at_end():
            char = self.peek()
            if char == "#":
                self.pos += 1
                name = self.read_ident()
                if not name:
                    raise self.error("после «#» нужен id")
                ids.append(name)
            elif char == ".":
                self.pos += 1
                name = self.read_ident()
                if not name:
                    raise self.error("после «.» нужно имя класса")
                classes.append(name)
            elif char == "[":
                attrs.append(self.parse_attribute())
            elif char == ":":
                pseudos.append(self.parse_pseudo())
            else:
                break
        if self.pos == start:
            if self.at_end():
                raise self.error("ожидался селектор")
            raise self.error(f"неожиданный символ «{self.peek()}»")
        return Compound(tag, tuple(ids), tuple(classes), tuple(attrs), tuple(pseudos))

    def parse_attribute(self) -> AttrTest:
        start = self.pos
        self.pos += 1
        self.skip_ws()
        name = self.read_ident().lower()
        if not name:
            raise self.error("в [] нужно имя атрибута")
        self.skip_ws()
        if self.peek() == "]":
            self.pos += 1
            return AttrTest(name, "")
        op = ""
        if self.peek() == "=":
            op = "="
            self.pos += 1
        elif self.text[self.pos : self.pos + 2] in ("~=", "|=", "^=", "$=", "*="):
            op = self.text[self.pos : self.pos + 2]
            self.pos += 2
        else:
            raise self.error("ожидался оператор сравнения атрибута")
        self.skip_ws()
        if self.peek() in ("'", '"'):
            value = self.read_string()
        else:
            out: list[str] = []
            while not self.at_end() and self.peek() not in " \t\r\n\f]":
                if self.peek() == "\\":
                    out.append(self.read_escape())
                else:
                    out.append(self.peek())
                    self.pos += 1
            value = "".join(out)
        self.skip_ws()
        caseless = name in _CASELESS_ATTRS
        flag = self.peek().lower()
        if flag in ("i", "s"):
            self.pos += 1
            caseless = flag == "i"
            self.skip_ws()
        if self.peek() != "]":
            if self.at_end():
                raise self.error("не закрыта скобка «[»", start)
            raise self.error(f"неожиданный символ «{self.peek()}»")
        self.pos += 1
        return AttrTest(name, op, value, caseless)

    def parse_pseudo(self) -> Pseudo:
        start = self.pos
        self.pos += 1
        if self.peek() == ":":
            raise self.error("псевдоэлементы (::) не поддерживаются", start)
        name = self.read_ident().lower()
        if not name:
            raise self.error("после «:» нужно имя псевдокласса")
        has_args = self.peek() == "("
        if has_args:
            self.pos += 1
        if name in _SIMPLE_PSEUDOS:
            if has_args:
                raise self.error(f":{name} не принимает аргументов", start)
            return Pseudo(name)
        if name in _NTH_PSEUDOS:
            if not has_args:
                raise self.error(f":{name}() требует аргумент", start)
            argument = self.read_raw_argument()
            return Pseudo(name, nth=self.parse_nth(argument, start))
        if name in _LIST_PSEUDOS:
            if not has_args:
                raise self.error(f":{name}() требует аргумент", start)
            inner = SelectorList(self.text, self.parse_list(nested=True))
            return Pseudo("is" if name in ("where", "matches") else name, selectors=inner)
        if name == "contains":
            if not has_args:
                raise self.error(":contains() требует текст", start)
            self.skip_ws()
            if self.peek() in ("'", '"'):
                text = self.read_string()
                self.skip_ws()
                if self.peek() != ")":
                    raise self.error("ожидалась «)»")
                self.pos += 1
            else:
                text = self.read_raw_argument().strip()
            return Pseudo(name, text=normalize_space(text).casefold())
        raise self.error(f"псевдокласс :{name} не поддерживается движком http", start)

    def parse_nth(self, argument: str, start: int) -> tuple[int, int]:
        value = "".join(argument.split()).lower()
        if value == "odd":
            return (2, 1)
        if value == "even":
            return (2, 0)
        if re.fullmatch(r"[+-]?\d+", value):
            return (0, int(value))
        match = _NTH_PATTERN.match(value)
        if match is None:
            raise self.error(f"некорректный аргумент «{argument.strip()}»", start)
        a_text = match.group(1)
        if a_text in ("", "+"):
            a = 1
        elif a_text == "-":
            a = -1
        else:
            a = int(a_text)
        b = int(match.group(2)) if match.group(2) else 0
        return (a, b)


@lru_cache(maxsize=512)
def compile_selector(text: str) -> SelectorList:
    """Compile a selector list; empty or invalid text raises SelectorError."""
    source = text if isinstance(text, str) else str(text)
    parser = _Parser(source)
    if not source.strip():
        raise parser.error("пустой селектор", 0)
    try:
        selectors = parser.parse_list()
    except RecursionError as exc:
        raise parser.error("слишком глубокая вложенность", 0) from exc
    return SelectorList(source, selectors)


# ---------------------------------------------------------------------------
# Matching


def _lower(value: str) -> str:
    return value.translate(_ASCII_LOWER)


def _match_attr(element: Element, test: AttrTest) -> bool:
    actual = element.attrs.get(test.name)
    if actual is None:
        return False
    op = test.op
    if not op:
        return True
    expected = test.value
    if test.caseless:
        actual = _lower(actual)
        expected = _lower(expected)
    if op == "=":
        return actual == expected
    if op == "~=":
        return bool(expected) and not any(c.isspace() for c in expected) and (
            expected in actual.split()
        )
    if op == "|=":
        return actual == expected or actual.startswith(expected + "-")
    if not expected:
        return False
    if op == "^=":
        return actual.startswith(expected)
    if op == "$=":
        return actual.endswith(expected)
    return expected in actual  # *=


def _nth_matches(position: int, a: int, b: int) -> bool:
    if a == 0:
        return position == b
    step, rest = divmod(position - b, a)
    return rest == 0 and step >= 0


def is_disabled(element: Element) -> bool:
    """The HTML "actually disabled" state for form controls."""
    if element.tag not in _FORM_CONTROLS:
        return False
    if "disabled" in element.attrs:
        return True
    if element.tag == "option":
        parent = element.parent
        return parent is not None and parent.tag == "optgroup" and "disabled" in parent.attrs
    child = element
    for ancestor in element.ancestors():
        if ancestor.tag == "fieldset" and "disabled" in ancestor.attrs:
            legend = next(
                (kid for kid in ancestor.element_children() if kid.tag == "legend"), None
            )
            if legend is None or child is not legend:
                return True
        child = ancestor
    return False


def _is_checked(element: Element) -> bool:
    live = element.attrs.get("data-logos-checked")
    if live is not None:
        return live == "1"
    live = element.attrs.get("data-logos-selected")
    if live is not None:
        return live == "1"
    if element.tag == "input":
        kind = _lower(element.attrs.get("type", ""))
        return kind in ("checkbox", "radio") and "checked" in element.attrs
    if element.tag == "option":
        return "selected" in element.attrs
    return False


def _match_pseudo(element: Element, pseudo: Pseudo) -> bool:
    name = pseudo.name
    if name == "first-child":
        return element.sibling_position()[0] == 1
    if name == "last-child":
        position, total = element.sibling_position()
        return position == total
    if name == "only-child":
        return element.sibling_position()[1] == 1
    if name == "first-of-type":
        return element.of_type_position()[0] == 1
    if name == "last-of-type":
        position, total = element.of_type_position()
        return position == total
    if name == "only-of-type":
        return element.of_type_position()[1] == 1
    if pseudo.nth is not None:
        a, b = pseudo.nth
        if name == "nth-child":
            return _nth_matches(element.sibling_position()[0], a, b)
        if name == "nth-last-child":
            position, total = element.sibling_position()
            return _nth_matches(total - position + 1, a, b)
        if name == "nth-of-type":
            return _nth_matches(element.of_type_position()[0], a, b)
        position, total = element.of_type_position()
        return _nth_matches(total - position + 1, a, b)
    if name == "not":
        assert pseudo.selectors is not None
        return not pseudo.selectors.matches(element)
    if name == "is":
        assert pseudo.selectors is not None
        return pseudo.selectors.matches(element)
    if name == "empty":
        return all(isinstance(child, str) and not child for child in element.children)
    if name == "root":
        return element.parent is not None and element.parent.tag == "#document"
    if name == "checked":
        return _is_checked(element)
    if name == "disabled":
        return is_disabled(element)
    if name == "enabled":
        return element.tag in _FORM_CONTROLS and not is_disabled(element)
    if name == "required":
        return element.tag in _FIELDS and "required" in element.attrs
    if name == "optional":
        return element.tag in _FIELDS and "required" not in element.attrs
    if name in ("link", "any-link"):
        return element.tag in ("a", "area") and "href" in element.attrs
    if name == "contains":
        needle = pseudo.text or ""
        return needle in normalize_space(element.text()).casefold()
    return False


def _match_compound(element: Element, compound: Compound) -> bool:
    if element.tag == "#document":
        return False
    if compound.tag is not None and element.tag != compound.tag:
        return False
    if compound.ids and any(element.attrs.get("id") != value for value in compound.ids):
        return False
    if compound.classes:
        classes = element.classes
        if any(name not in classes for name in compound.classes):
            return False
    for test in compound.attrs:
        if not _match_attr(element, test):
            return False
    for pseudo in compound.pseudos:
        if not _match_pseudo(element, pseudo):
            return False
    return True


def _element_parent(element: Element) -> Element | None:
    parent = element.parent
    if parent is None or parent.tag == "#document":
        return None
    return parent


def _candidates(element: Element, combinator: str) -> Iterator[Element]:
    if combinator == ">":
        parent = _element_parent(element)
        if parent is not None:
            yield parent
        return
    if combinator == " ":
        node = _element_parent(element)
        while node is not None:
            yield node
            node = _element_parent(node)
        return
    parent = element.parent
    if parent is None:
        return
    siblings = parent.element_children()
    position = element.sibling_position()[0] - 1
    if combinator == "+":
        if position > 0:
            yield siblings[position - 1]
        return
    for index in range(position - 1, -1, -1):  # "~"
        yield siblings[index]


def _match_complex(element: Element, selector: Complex) -> bool:
    parts = selector.parts
    last = len(parts) - 1
    if not _match_compound(element, parts[last]):
        return False
    if last == 0:
        return True
    combinators = selector.combinators
    stack: list[tuple[int, Iterator[Element]]] = [
        (last - 1, _candidates(element, combinators[last - 1]))
    ]
    while stack:
        part, candidates = stack[-1]
        found = None
        for candidate in candidates:
            if _match_compound(candidate, parts[part]):
                found = candidate
                break
        if found is None:
            stack.pop()
            continue
        if part == 0:
            return True
        stack.append((part - 1, _candidates(found, combinators[part - 1])))
    return False


def _compiled(sel: str | SelectorList) -> SelectorList:
    return sel if isinstance(sel, SelectorList) else compile_selector(sel)


def matches(element: Element, sel: str | SelectorList) -> bool:
    """True when ``element`` matches the selector list."""
    return _compiled(sel).matches(element)


def select(root: Element | Document, sel: str | SelectorList) -> list[Element]:
    """Descendants of ``root`` matching ``sel``, in document order, root excluded."""
    compiled = _compiled(sel)
    start = root.root if isinstance(root, Document) else root
    return [element for element in start.descendants() if compiled.matches(element)]


def select_one(root: Element | Document, sel: str | SelectorList) -> Element | None:
    compiled = _compiled(sel)
    start = root.root if isinstance(root, Document) else root
    for element in start.descendants():
        if compiled.matches(element):
            return element
    return None


__all__ = [
    "SelectorError",
    "SelectorList",
    "compile_selector",
    "is_disabled",
    "matches",
    "select",
    "select_one",
]
