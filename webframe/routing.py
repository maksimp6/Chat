"""URL rules and their matching: ``/api/files/<file_id>``, ``<path:name>``, methods."""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable, Iterable
from typing import Any

from webframe.exceptions import MethodNotAllowed, NotFound

# Converter name -> (regex of the matched text, Python value of the match).
CONVERTERS: dict[str, tuple[str, Callable[[str], Any]]] = {
    "default": (r"[^/]+", str),
    "string": (r"[^/]+", str),
    "path": (r"[^/].*?", str),
    "int": (r"\d+", int),
    "float": (r"\d+\.\d+", float),
    "uuid": (
        r"[A-Fa-f0-9]{8}-[A-Fa-f0-9]{4}-[A-Fa-f0-9]{4}-[A-Fa-f0-9]{4}-[A-Fa-f0-9]{12}",
        uuid.UUID,
    ),
}

_PART_RE = re.compile(
    r"<(?:(?P<converter>[a-zA-Z_][a-zA-Z0-9_]*)(?:\((?P<args>.*?)\))?:)?"
    r"(?P<name>[a-zA-Z_][a-zA-Z0-9_]*)>"
)


class RequestRedirect(Exception):
    """The path matches a rule only with its trailing slash: the client is sent there."""

    code = 308

    def __init__(self, new_path: str) -> None:
        super().__init__(new_path)
        self.new_path = new_path


class Rule:
    """One URL rule: the pattern, the endpoint it names and the methods it answers."""

    def __init__(
        self,
        rule: str,
        endpoint: str,
        methods: Iterable[str] | None = None,
        defaults: dict[str, Any] | None = None,
    ) -> None:
        if not rule.startswith("/"):
            raise ValueError(f"URL rule {rule!r} must start with a slash")
        self.rule = rule
        self.endpoint = endpoint
        self.defaults = dict(defaults or {})
        methods_set = {method.upper() for method in (methods or ("GET",))}
        if "GET" in methods_set:
            methods_set.add("HEAD")
        self.provide_automatic_options = "OPTIONS" not in methods_set
        if self.provide_automatic_options:
            methods_set.add("OPTIONS")
        self.methods = frozenset(methods_set)
        self._converters: dict[str, Callable[[str], Any]] = {}
        self._regex, self._static_weight, self._path_parts = self._compile(rule)
        self.arguments = frozenset(self._converters)

    def _compile(self, rule: str) -> tuple[re.Pattern[str], int, int]:
        pattern = ["^"]
        static_weight = 0
        path_parts = 0
        position = 0
        for match in _PART_RE.finditer(rule):
            static = rule[position : match.start()]
            pattern.append(re.escape(static))
            static_weight += len(static)
            converter = match.group("converter") or "default"
            if converter not in CONVERTERS:
                raise LookupError(f"the converter {converter!r} does not exist")
            regex, to_python = CONVERTERS[converter]
            if converter == "path":
                path_parts += 1
            name = match.group("name")
            if name in self._converters:
                raise ValueError(f"variable name {name!r} used twice in {rule!r}")
            self._converters[name] = to_python
            pattern.append(f"(?P<{name}>{regex})")
            position = match.end()
        static = rule[position:]
        pattern.append(re.escape(static))
        static_weight += len(static)
        pattern.append("$")
        return re.compile("".join(pattern)), static_weight, path_parts

    @property
    def sort_key(self) -> tuple[bool, int, int]:
        # Rules with no variables first, then the ones with more fixed text; a
        # ``path`` variable, which swallows slashes, comes after the others.
        return (bool(self.arguments), -self._static_weight, self._path_parts)

    def match(self, path: str) -> dict[str, Any] | None:
        found = self._regex.match(path)
        if found is None:
            return None
        values = dict(self.defaults)
        for name, text in found.groupdict().items():
            try:
                values[name] = self._converters[name](text)
            except ValueError:
                return None
        return values

    def __repr__(self) -> str:
        return f"<Rule {self.rule!r} -> {self.endpoint} {sorted(self.methods)}>"


class Router:
    """The rules of an application, matched most specific first."""

    def __init__(self) -> None:
        self._rules: list[Rule] = []
        self._sorted = True

    def add(self, rule: Rule) -> None:
        self._rules.append(rule)
        self._sorted = False

    @property
    def rules(self) -> list[Rule]:
        if not self._sorted:
            self._rules.sort(key=lambda rule: rule.sort_key)
            self._sorted = True
        return self._rules

    def match(self, path: str, method: str) -> tuple[Rule, dict[str, Any]]:
        """The rule for ``path`` and ``method`` with the values of its variables.

        ``NotFound`` when no rule matches the path, ``MethodNotAllowed`` (naming the
        allowed methods) when rules match the path but not the method, and
        ``RequestRedirect`` when only the path with a trailing slash matches.
        """
        method = method.upper()
        allowed: set[str] = set()
        for rule in self.rules:
            values = rule.match(path)
            if values is None:
                continue
            if method in rule.methods:
                return rule, values
            allowed.update(rule.methods)
        if allowed:
            raise MethodNotAllowed(allowed)
        if not path.endswith("/") and method in ("GET", "HEAD"):
            slashed = path + "/"
            for rule in self.rules:
                if rule.rule.endswith("/") and rule.match(slashed) is not None:
                    raise RequestRedirect(slashed)
        raise NotFound()

    def allowed_methods(self, path: str) -> set[str]:
        allowed: set[str] = set()
        for rule in self.rules:
            if rule.match(path) is not None:
                allowed.update(rule.methods)
        return allowed

    def __iter__(self):
        return iter(self.rules)

    def __len__(self) -> int:
        return len(self._rules)


__all__ = ["CONVERTERS", "RequestRedirect", "Router", "Rule"]
