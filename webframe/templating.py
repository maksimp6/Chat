"""HTML templates: ``{{ expression|filter }}``, ``{% set %}``, ``{% if %}``, ``{% for %}``.

The syntax is the familiar one of ``templates/index.html``: expressions with
``~`` for text concatenation, ``a if cond else b``, attribute and item access,
slices and filters (``tojson``, ``safe``, ``default`` and the common string and
sequence ones). Output is HTML-escaped unless the value is ``Markup``.
Templates are parsed once and reparsed when their file changes.
"""

from __future__ import annotations

import html
import json as _json
import os
import re
from collections.abc import Callable, Iterator
from typing import Any

# --------------------------------------------------------------------------- values


class Markup(str):
    """Text that is already HTML and is not escaped again."""

    def __html__(self) -> Markup:
        return self

    def __add__(self, other: Any) -> Markup:
        return Markup(str(self) + str(escape(other)))

    def __repr__(self) -> str:
        return f"Markup({str.__repr__(self)})"


class Undefined:
    """A name the template does not know: empty, false, nothing to iterate."""

    __slots__ = ("_name",)

    def __init__(self, name: str = "") -> None:
        self._name = name

    def __bool__(self) -> bool:
        return False

    def __str__(self) -> str:
        return ""

    def __iter__(self) -> Iterator[Any]:
        return iter(())

    def __len__(self) -> int:
        return 0

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Undefined)

    def __hash__(self) -> int:
        return hash(Undefined)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)
        raise TemplateError(f"{self._name!r} is undefined")

    def __getitem__(self, key: Any) -> Any:
        raise TemplateError(f"{self._name!r} is undefined")

    def __repr__(self) -> str:
        return f"Undefined({self._name!r})"


class TemplateError(Exception):
    """A template could not be parsed or rendered."""


def escape(value: Any) -> Markup:
    if hasattr(value, "__html__"):
        return Markup(value.__html__())
    if value is None:
        return Markup("None")
    return Markup(
        html.escape(str(value), quote=True).replace("&#x27;", "&#39;")
    )


def _htmlsafe_json(value: Any, dumps: Callable[[Any], str]) -> Markup:
    text = dumps(value)
    return Markup(
        text.replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("'", "\\u0027")
    )


# --------------------------------------------------------------------------- filters


def _filter_default(value: Any, default: Any = "", boolean: bool = False) -> Any:
    if isinstance(value, Undefined) or (boolean and not value):
        return default
    return value


def _filter_join(value: Any, separator: str = "", attribute: str | None = None) -> str:
    items = value
    if attribute is not None:
        items = [_getattr(item, attribute) for item in value]
    return separator.join(str(item) for item in items)


def _filter_replace(value: Any, old: str, new: str, count: int | None = None) -> str:
    text = str(value)
    return text.replace(old, new) if count is None else text.replace(old, new, count)


def _filter_truncate(
    value: Any, length: int = 255, killwords: bool = False, end: str = "..."
) -> str:
    text = str(value)
    if len(text) <= length:
        return text
    cut = text[: max(0, length - len(end))]
    if not killwords:
        cut = cut.rsplit(" ", 1)[0]
    return cut + end


def _filter_urlencode(value: Any) -> str:
    from urllib.parse import quote, urlencode

    if isinstance(value, dict):
        return urlencode(value)
    return quote(str(value), safe="/")


FILTERS: dict[str, Callable[..., Any]] = {
    "abs": abs,
    "capitalize": lambda value: str(value).capitalize(),
    "count": len,
    "d": _filter_default,
    "default": _filter_default,
    "e": escape,
    "escape": escape,
    "first": lambda value: next(iter(value)),
    "float": lambda value, default=0.0: _convert(float, value, default),
    "int": lambda value, default=0: _convert(int, value, default),
    "items": lambda value: list(value.items()),
    "join": _filter_join,
    "last": lambda value: list(value)[-1],
    "length": len,
    "list": list,
    "lower": lambda value: str(value).lower(),
    "replace": _filter_replace,
    "reverse": lambda value: list(reversed(list(value))),
    "round": lambda value, precision=0: round(value, precision),
    "safe": lambda value: Markup(value),
    "sort": lambda value, reverse=False: sorted(value, reverse=reverse),
    "string": lambda value: value if isinstance(value, Markup) else str(value),
    "title": lambda value: str(value).title(),
    "trim": lambda value: str(value).strip(),
    "truncate": _filter_truncate,
    "upper": lambda value: str(value).upper(),
    "urlencode": _filter_urlencode,
}

TESTS: dict[str, Callable[..., bool]] = {
    "defined": lambda value: not isinstance(value, Undefined),
    "undefined": lambda value: isinstance(value, Undefined),
    "none": lambda value: value is None,
    "string": lambda value: isinstance(value, str),
    "number": lambda value: isinstance(value, (int, float)) and not isinstance(value, bool),
    "mapping": lambda value: isinstance(value, dict),
    "iterable": lambda value: hasattr(value, "__iter__"),
    "true": lambda value: value is True,
    "false": lambda value: value is False,
    "even": lambda value: value % 2 == 0,
    "odd": lambda value: value % 2 == 1,
    "divisibleby": lambda value, num: value % num == 0,
    "equalto": lambda value, other: value == other,
    "eq": lambda value, other: value == other,
}


def _convert(kind: Callable[[Any], Any], value: Any, default: Any) -> Any:
    try:
        return kind(value)
    except (TypeError, ValueError):
        return default


def _getattr(obj: Any, name: str) -> Any:
    """``obj.name`` as Jinja reads it: the attribute, else the item, else undefined."""
    if isinstance(obj, Undefined):
        raise TemplateError(f"{obj._name!r} is undefined")
    try:
        return getattr(obj, name)
    except AttributeError:
        pass
    try:
        return obj[name]
    except (KeyError, TypeError, IndexError):
        return Undefined(name)


def _getitem(obj: Any, key: Any) -> Any:
    if isinstance(obj, Undefined):
        raise TemplateError(f"{obj._name!r} is undefined")
    try:
        return obj[key]
    except (KeyError, IndexError, TypeError):
        if isinstance(key, str):
            try:
                return getattr(obj, key)
            except AttributeError:
                pass
        return Undefined(str(key))


# --------------------------------------------------------------------------- expression lexer

_TOKEN_RE = re.compile(
    r"""
    (?P<ws>\s+)
  | (?P<number>\d+(?:\.\d+)?)
  | (?P<string>"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')
  | (?P<name>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<op>\*\*|//|==|!=|<=|>=|[-+*/%~|.,:()\[\]{}<>=])
    """,
    re.VERBOSE,
)


def _tokenize(source: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    position = 0
    while position < len(source):
        match = _TOKEN_RE.match(source, position)
        if match is None:
            raise TemplateError(f"unexpected character {source[position]!r} in {source!r}")
        position = match.end()
        kind = match.lastgroup or ""
        if kind == "ws":
            continue
        tokens.append((kind, match.group(kind)))
    tokens.append(("eof", ""))
    return tokens


# --------------------------------------------------------------------------- expression nodes

Context = dict[str, Any]


class Expr:
    def eval(self, ctx: Context) -> Any:
        raise NotImplementedError


class Const(Expr):
    def __init__(self, value: Any) -> None:
        self.value = value

    def eval(self, ctx: Context) -> Any:
        return self.value


class Name(Expr):
    def __init__(self, name: str) -> None:
        self.name = name

    def eval(self, ctx: Context) -> Any:
        try:
            return ctx[self.name]
        except KeyError:
            return Undefined(self.name)


class ListExpr(Expr):
    def __init__(self, items: list[Expr]) -> None:
        self.items = items

    def eval(self, ctx: Context) -> Any:
        return [item.eval(ctx) for item in self.items]


class TupleExpr(ListExpr):
    def eval(self, ctx: Context) -> Any:
        return tuple(item.eval(ctx) for item in self.items)


class DictExpr(Expr):
    def __init__(self, items: list[tuple[Expr, Expr]]) -> None:
        self.items = items

    def eval(self, ctx: Context) -> Any:
        return {key.eval(ctx): value.eval(ctx) for key, value in self.items}


class Attr(Expr):
    def __init__(self, target: Expr, name: str) -> None:
        self.target = target
        self.name = name

    def eval(self, ctx: Context) -> Any:
        return _getattr(self.target.eval(ctx), self.name)


class Item(Expr):
    def __init__(self, target: Expr, key: Expr) -> None:
        self.target = target
        self.key = key

    def eval(self, ctx: Context) -> Any:
        return _getitem(self.target.eval(ctx), self.key.eval(ctx))


class Slice(Expr):
    def __init__(self, target: Expr, start: Expr | None, stop: Expr | None, step: Expr | None) -> None:
        self.target = target
        self.start, self.stop, self.step = start, stop, step

    def eval(self, ctx: Context) -> Any:
        target = self.target.eval(ctx)
        if isinstance(target, Undefined):
            raise TemplateError(f"{target._name!r} is undefined")
        return target[
            slice(
                None if self.start is None else self.start.eval(ctx),
                None if self.stop is None else self.stop.eval(ctx),
                None if self.step is None else self.step.eval(ctx),
            )
        ]


class Call(Expr):
    def __init__(self, target: Expr, args: list[Expr], kwargs: list[tuple[str, Expr]]) -> None:
        self.target, self.args, self.kwargs = target, args, kwargs

    def eval(self, ctx: Context) -> Any:
        func = self.target.eval(ctx)
        if isinstance(func, Undefined):
            raise TemplateError(f"{func._name!r} is undefined")
        return func(
            *[arg.eval(ctx) for arg in self.args],
            **{name: value.eval(ctx) for name, value in self.kwargs},
        )


class Filter(Expr):
    def __init__(self, target: Expr, name: str, args: list[Expr], kwargs: list[tuple[str, Expr]]) -> None:
        self.target, self.name, self.args, self.kwargs = target, name, args, kwargs

    def eval(self, ctx: Context) -> Any:
        filters = ctx.get("__filters__", FILTERS)
        try:
            func = filters[self.name]
        except KeyError:
            raise TemplateError(f"no filter named {self.name!r}") from None
        return func(
            self.target.eval(ctx),
            *[arg.eval(ctx) for arg in self.args],
            **{name: value.eval(ctx) for name, value in self.kwargs},
        )


class Test(Expr):
    def __init__(self, target: Expr, name: str, args: list[Expr], negate: bool) -> None:
        self.target, self.name, self.args, self.negate = target, name, args, negate

    def eval(self, ctx: Context) -> Any:
        try:
            func = TESTS[self.name]
        except KeyError:
            raise TemplateError(f"no test named {self.name!r}") from None
        result = bool(func(self.target.eval(ctx), *[arg.eval(ctx) for arg in self.args]))
        return not result if self.negate else result


class Unary(Expr):
    def __init__(self, op: str, operand: Expr) -> None:
        self.op, self.operand = op, operand

    def eval(self, ctx: Context) -> Any:
        value = self.operand.eval(ctx)
        if self.op == "not":
            return not value
        if self.op == "-":
            return -value
        return +value


class Binary(Expr):
    def __init__(self, op: str, left: Expr, right: Expr) -> None:
        self.op, self.left, self.right = op, left, right

    def eval(self, ctx: Context) -> Any:
        op = self.op
        if op == "and":
            left = self.left.eval(ctx)
            return self.right.eval(ctx) if left else left
        if op == "or":
            left = self.left.eval(ctx)
            return left if left else self.right.eval(ctx)
        left = self.left.eval(ctx)
        right = self.right.eval(ctx)
        if op == "~":
            return _concat(left) + _concat(right)
        if op == "+":
            return left + right
        if op == "-":
            return left - right
        if op == "*":
            return left * right
        if op == "/":
            return left / right
        if op == "//":
            return left // right
        if op == "%":
            return left % right
        if op == "**":
            return left**right
        if op == "==":
            return left == right
        if op == "!=":
            return left != right
        if op == "<":
            return left < right
        if op == ">":
            return left > right
        if op == "<=":
            return left <= right
        if op == ">=":
            return left >= right
        if op == "in":
            return left in right
        if op == "not in":
            return left not in right
        raise TemplateError(f"unknown operator {op!r}")


def _concat(value: Any) -> str:
    if isinstance(value, Undefined):
        return ""
    return str(value)


class Conditional(Expr):
    def __init__(self, test: Expr, true: Expr, false: Expr | None) -> None:
        self.test, self.true, self.false = test, true, false

    def eval(self, ctx: Context) -> Any:
        if self.test.eval(ctx):
            return self.true.eval(ctx)
        if self.false is None:
            return Undefined("")
        return self.false.eval(ctx)


# --------------------------------------------------------------------------- expression parser

_COMPARE_OPS = {"==", "!=", "<", ">", "<=", ">="}
_LITERALS = {"true": True, "True": True, "false": False, "False": False, "none": None, "None": None}


class ExpressionParser:
    def __init__(self, source: str) -> None:
        self.source = source
        self.tokens = _tokenize(source)
        self.position = 0

    # token helpers

    def peek(self, offset: int = 0) -> tuple[str, str]:
        return self.tokens[min(self.position + offset, len(self.tokens) - 1)]

    def next(self) -> tuple[str, str]:
        token = self.tokens[self.position]
        if token[0] != "eof":
            self.position += 1
        return token

    def at(self, kind: str, value: str | None = None) -> bool:
        token = self.peek()
        return token[0] == kind and (value is None or token[1] == value)

    def at_name(self, value: str) -> bool:
        return self.at("name", value)

    def expect(self, kind: str, value: str | None = None) -> str:
        token = self.next()
        if token[0] != kind or (value is not None and token[1] != value):
            wanted = value if value is not None else kind
            raise TemplateError(f"expected {wanted!r}, got {token[1]!r} in {self.source!r}")
        return token[1]

    def parse(self) -> Expr:
        expr = self.parse_expression()
        if not self.at("eof"):
            raise TemplateError(f"unexpected {self.peek()[1]!r} in {self.source!r}")
        return expr

    # grammar, lowest precedence first

    def parse_expression(self) -> Expr:
        expr = self.parse_or()
        if self.at_name("if"):
            self.next()
            test = self.parse_or()
            false = None
            if self.at_name("else"):
                self.next()
                false = self.parse_expression()
            return Conditional(test, expr, false)
        return expr

    def parse_or(self) -> Expr:
        left = self.parse_and()
        while self.at_name("or"):
            self.next()
            left = Binary("or", left, self.parse_and())
        return left

    def parse_and(self) -> Expr:
        left = self.parse_not()
        while self.at_name("and"):
            self.next()
            left = Binary("and", left, self.parse_not())
        return left

    def parse_not(self) -> Expr:
        if self.at_name("not"):
            self.next()
            return Unary("not", self.parse_not())
        return self.parse_compare()

    def parse_compare(self) -> Expr:
        left = self.parse_concat()
        while True:
            token = self.peek()
            if token[0] == "op" and token[1] in _COMPARE_OPS:
                self.next()
                left = Binary(token[1], left, self.parse_concat())
            elif token == ("name", "in"):
                self.next()
                left = Binary("in", left, self.parse_concat())
            elif token == ("name", "not") and self.peek(1) == ("name", "in"):
                self.next()
                self.next()
                left = Binary("not in", left, self.parse_concat())
            elif token == ("name", "is"):
                self.next()
                negate = False
                if self.at_name("not"):
                    self.next()
                    negate = True
                name = self.expect("name")
                args: list[Expr] = []
                if self.at("op", "("):
                    self.next()
                    args, _ = self.parse_arguments()
                elif self.peek()[0] in ("name", "number", "string") and not self.at_name_in(
                    "and", "or", "if", "else", "is", "in", "not"
                ):
                    args = [self.parse_concat()]
                left = Test(left, name, args, negate)
            else:
                return left

    def at_name_in(self, *names: str) -> bool:
        token = self.peek()
        return token[0] == "name" and token[1] in names

    def parse_concat(self) -> Expr:
        left = self.parse_math1()
        while self.at("op", "~"):
            self.next()
            left = Binary("~", left, self.parse_math1())
        return left

    def parse_math1(self) -> Expr:
        left = self.parse_math2()
        while self.at("op", "+") or self.at("op", "-"):
            op = self.next()[1]
            left = Binary(op, left, self.parse_math2())
        return left

    def parse_math2(self) -> Expr:
        left = self.parse_pow()
        while self.peek()[0] == "op" and self.peek()[1] in ("*", "/", "//", "%"):
            op = self.next()[1]
            left = Binary(op, left, self.parse_pow())
        return left

    def parse_pow(self) -> Expr:
        left = self.parse_unary()
        while self.at("op", "**"):
            self.next()
            left = Binary("**", left, self.parse_unary())
        return left

    def parse_unary(self) -> Expr:
        if self.at("op", "-") or self.at("op", "+"):
            op = self.next()[1]
            return Unary(op, self.parse_unary())
        return self.parse_postfix(self.parse_primary())

    def parse_primary(self) -> Expr:
        kind, value = self.next()
        if kind == "number":
            return Const(float(value) if "." in value else int(value))
        if kind == "string":
            text = _unescape_string(value)
            while self.at("string"):
                text += _unescape_string(self.next()[1])
            return Const(text)
        if kind == "name":
            if value in _LITERALS:
                return Const(_LITERALS[value])
            return Name(value)
        if kind == "op" and value == "(":
            if self.at("op", ")"):
                self.next()
                return TupleExpr([])
            first = self.parse_expression()
            if self.at("op", ","):
                items = [first]
                while self.at("op", ","):
                    self.next()
                    if self.at("op", ")"):
                        break
                    items.append(self.parse_expression())
                self.expect("op", ")")
                return TupleExpr(items)
            self.expect("op", ")")
            return first
        if kind == "op" and value == "[":
            items = []
            while not self.at("op", "]"):
                items.append(self.parse_expression())
                if not self.at("op", ","):
                    break
                self.next()
            self.expect("op", "]")
            return ListExpr(items)
        if kind == "op" and value == "{":
            pairs: list[tuple[Expr, Expr]] = []
            while not self.at("op", "}"):
                key = self.parse_expression()
                self.expect("op", ":")
                pairs.append((key, self.parse_expression()))
                if not self.at("op", ","):
                    break
                self.next()
            self.expect("op", "}")
            return DictExpr(pairs)
        raise TemplateError(f"unexpected {value!r} in {self.source!r}")

    def parse_postfix(self, expr: Expr) -> Expr:
        while True:
            if self.at("op", "."):
                self.next()
                token = self.next()
                if token[0] not in ("name", "number"):
                    raise TemplateError(f"expected a name after '.' in {self.source!r}")
                expr = Attr(expr, token[1]) if token[0] == "name" else Item(expr, Const(int(token[1])))
            elif self.at("op", "["):
                self.next()
                expr = self.parse_subscript(expr)
            elif self.at("op", "("):
                self.next()
                args, kwargs = self.parse_arguments()
                expr = Call(expr, args, kwargs)
            elif self.at("op", "|"):
                self.next()
                name = self.expect("name")
                args, kwargs = [], []
                if self.at("op", "("):
                    self.next()
                    args, kwargs = self.parse_arguments()
                expr = Filter(expr, name, args, kwargs)
            else:
                return expr

    def parse_subscript(self, target: Expr) -> Expr:
        start = stop = step = None
        if not self.at("op", ":"):
            start = self.parse_expression()
            if self.at("op", "]"):
                self.next()
                return Item(target, start)
        self.expect("op", ":")
        if not self.at("op", ":") and not self.at("op", "]"):
            stop = self.parse_expression()
        if self.at("op", ":"):
            self.next()
            if not self.at("op", "]"):
                step = self.parse_expression()
        self.expect("op", "]")
        return Slice(target, start, stop, step)

    def parse_arguments(self) -> tuple[list[Expr], list[tuple[str, Expr]]]:
        """Arguments up to and including the closing parenthesis."""
        args: list[Expr] = []
        kwargs: list[tuple[str, Expr]] = []
        while not self.at("op", ")"):
            if self.peek()[0] == "name" and self.peek(1) == ("op", "="):
                name = self.next()[1]
                self.next()
                kwargs.append((name, self.parse_expression()))
            else:
                args.append(self.parse_expression())
            if not self.at("op", ","):
                break
            self.next()
        self.expect("op", ")")
        return args, kwargs


def _unescape_string(token: str) -> str:
    body = token[1:-1]
    return re.sub(
        r"\\(.)",
        lambda match: {"n": "\n", "t": "\t", "r": "\r"}.get(match.group(1), match.group(1)),
        body,
    )


def compile_expression(source: str) -> Expr:
    return ExpressionParser(source.strip()).parse()


# --------------------------------------------------------------------------- template nodes


class Node:
    def render(self, ctx: Context, out: list[str]) -> None:
        raise NotImplementedError


class Text(Node):
    def __init__(self, text: str) -> None:
        self.text = text

    def render(self, ctx: Context, out: list[str]) -> None:
        out.append(self.text)


class Output(Node):
    def __init__(self, expr: Expr) -> None:
        self.expr = expr

    def render(self, ctx: Context, out: list[str]) -> None:
        value = self.expr.eval(ctx)
        if isinstance(value, Undefined):
            return
        out.append(str(escape(value)) if ctx.get("__autoescape__", True) else str(value))


class Set(Node):
    def __init__(self, name: str, expr: Expr) -> None:
        self.name, self.expr = name, expr

    def render(self, ctx: Context, out: list[str]) -> None:
        ctx[self.name] = self.expr.eval(ctx)


class If(Node):
    def __init__(self) -> None:
        self.branches: list[tuple[Expr, list[Node]]] = []
        self.otherwise: list[Node] | None = None

    def render(self, ctx: Context, out: list[str]) -> None:
        for test, body in self.branches:
            if test.eval(ctx):
                _render_all(body, ctx, out)
                return
        if self.otherwise is not None:
            _render_all(self.otherwise, ctx, out)


class For(Node):
    def __init__(self, targets: list[str], iterable: Expr) -> None:
        self.targets = targets
        self.iterable = iterable
        self.body: list[Node] = []
        self.otherwise: list[Node] | None = None

    def render(self, ctx: Context, out: list[str]) -> None:
        items = list(self.iterable.eval(ctx))
        if not items:
            if self.otherwise is not None:
                _render_all(self.otherwise, ctx, out)
            return
        saved = {name: ctx[name] for name in self.targets + ["loop"] if name in ctx}
        total = len(items)
        for index, item in enumerate(items):
            if len(self.targets) == 1:
                ctx[self.targets[0]] = item
            else:
                for name, value in zip(self.targets, item, strict=False):
                    ctx[name] = value
            ctx["loop"] = {
                "index": index + 1,
                "index0": index,
                "revindex": total - index,
                "revindex0": total - index - 1,
                "first": index == 0,
                "last": index == total - 1,
                "length": total,
            }
            _render_all(self.body, ctx, out)
        for name in self.targets + ["loop"]:
            ctx.pop(name, None)
        ctx.update(saved)


def _render_all(nodes: list[Node], ctx: Context, out: list[str]) -> None:
    for node in nodes:
        node.render(ctx, out)


# --------------------------------------------------------------------------- template parser

_TAG_RE = re.compile(
    r"(?P<var>\{\{-?\s*(?P<var_body>.*?)\s*-?\}\})"
    r"|(?P<block>\{%-?\s*(?P<block_body>.*?)\s*-?%\})"
    r"|(?P<comment>\{#.*?#\})",
    re.S,
)


def _lex_template(source: str) -> list[tuple[str, str]]:
    """``("text", ...)``, ``("var", expr)`` and ``("block", statement)`` tokens."""
    tokens: list[tuple[str, str]] = []
    position = 0
    strip_next = False
    for match in _TAG_RE.finditer(source):
        text = source[position : match.start()]
        tag = match.group(0)
        if strip_next:
            text = text.lstrip()
        if tag.startswith(("{{-", "{%-")):
            text = text.rstrip()
        if text:
            tokens.append(("text", text))
        strip_next = tag.endswith(("-}}", "-%}"))
        if match.group("var") is not None:
            tokens.append(("var", match.group("var_body")))
        elif match.group("block") is not None:
            tokens.append(("block", match.group("block_body")))
        position = match.end()
    text = source[position:]
    if strip_next:
        text = text.lstrip()
    if text:
        tokens.append(("text", text))
    return tokens


class TemplateParser:
    def __init__(self, source: str, name: str) -> None:
        self.name = name
        self.tokens = _lex_template(source)
        self.position = 0

    def parse(self) -> list[Node]:
        nodes, end = self.parse_until(())
        if end is not None:
            raise TemplateError(f"{self.name}: unexpected {{% {end} %}}")
        return nodes

    def parse_until(self, ends: tuple[str, ...]) -> tuple[list[Node], str | None]:
        nodes: list[Node] = []
        while self.position < len(self.tokens):
            kind, body = self.tokens[self.position]
            self.position += 1
            if kind == "text":
                nodes.append(Text(body))
            elif kind == "var":
                nodes.append(Output(compile_expression(body)))
            else:
                keyword, _, rest = body.strip().partition(" ")
                keyword = keyword.strip()
                if keyword in ends:
                    return nodes, body.strip()
                nodes.append(self.parse_statement(keyword, rest.strip()))
        if ends:
            raise TemplateError(f"{self.name}: missing {{% {ends[-1]} %}}")
        return nodes, None

    def parse_statement(self, keyword: str, rest: str) -> Node:
        if keyword == "set":
            name, sep, expr = rest.partition("=")
            if not sep or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name.strip()):
                raise TemplateError(f"{self.name}: malformed set {rest!r}")
            return Set(name.strip(), compile_expression(expr))
        if keyword == "if":
            node = If()
            test = compile_expression(rest)
            while True:
                body, end = self.parse_until(("elif", "else", "endif"))
                node.branches.append((test, body))
                if end is None or end == "endif":
                    return node
                if end.startswith("elif"):
                    test = compile_expression(end[4:])
                    continue
                node.otherwise, end = self.parse_until(("endif",))
                return node
        if keyword == "for":
            targets_text, sep, iterable = rest.partition(" in ")
            if not sep:
                raise TemplateError(f"{self.name}: malformed for {rest!r}")
            targets = [name.strip() for name in targets_text.split(",") if name.strip()]
            node = For(targets, compile_expression(iterable))
            node.body, end = self.parse_until(("else", "endfor"))
            if end == "else":
                node.otherwise, end = self.parse_until(("endfor",))
            return node
        raise TemplateError(f"{self.name}: unknown statement {keyword!r}")


class Template:
    """A parsed template; ``render(**context)`` produces the page."""

    def __init__(self, source: str, name: str = "<template>", autoescape: bool = True) -> None:
        if source.endswith("\n"):
            source = source[:-1]
        self.name = name
        self.autoescape = autoescape
        self.nodes = TemplateParser(source, name).parse()

    def render(self, *args: Any, **context: Any) -> str:
        ctx: Context = {}
        for mapping in args:
            ctx.update(mapping)
        ctx.update(context)
        ctx.setdefault("__autoescape__", self.autoescape)
        ctx.setdefault("__filters__", _filters_with_json(ctx.pop("__json_dumps__", None)))
        ctx.setdefault("range", range)
        out: list[str] = []
        _render_all(self.nodes, ctx, out)
        return "".join(out)


def _filters_with_json(dumps: Callable[[Any], str] | None) -> dict[str, Callable[..., Any]]:
    serializer = dumps or (lambda value: _json.dumps(value, ensure_ascii=False))
    filters = dict(FILTERS)
    filters["tojson"] = lambda value: _htmlsafe_json(value, serializer)
    return filters


class TemplateLoader:
    """Templates of one folder, parsed on first use and again when a file changes."""

    def __init__(self, folder: str) -> None:
        self.folder = folder
        self._cache: dict[str, tuple[tuple[int, int], Template]] = {}

    def get_template(self, name: str) -> Template:
        path = os.path.normpath(os.path.join(self.folder, name))
        if not path.startswith(os.path.normpath(self.folder) + os.sep):
            raise TemplateError(f"template {name!r} is outside the template folder")
        try:
            stat = os.stat(path)
        except OSError:
            raise TemplateError(f"template {name!r} not found") from None
        key = (stat.st_mtime_ns, stat.st_size)
        cached = self._cache.get(name)
        if cached is not None and cached[0] == key:
            return cached[1]
        with open(path, encoding="utf-8") as handle:
            template = Template(handle.read(), name=name)
        self._cache[name] = (key, template)
        return template

    def render(self, name: str, context: dict[str, Any], json_dumps: Callable[[Any], str] | None = None) -> str:
        return self.get_template(name).render(context, __json_dumps__=json_dumps)


__all__ = [
    "FILTERS",
    "Markup",
    "TESTS",
    "Template",
    "TemplateError",
    "TemplateLoader",
    "Undefined",
    "compile_expression",
    "escape",
]
