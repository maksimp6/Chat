"""Shared JSON graph validation without filesystem or store lifecycle policy."""

import math
from collections.abc import Callable, Iterator
from itertools import chain
from typing import Any


def walk_value(
    value: Any,
    *,
    max_depth: int,
    max_nodes: int,
    max_text_bytes: int,
    error_type: type[RuntimeError],
    limit_error_type: type[RuntimeError],
) -> Iterator[Any]:
    """Bound traversal before copying/encoding, counting repeated subtrees."""
    stack: list[tuple[Iterator[Any], int, int | None]] = [(iter((value,)), 0, None)]
    active: set[int] = set()
    nodes = 0
    text_bytes = 0
    while stack:
        children, depth, identity = stack[-1]
        try:
            item = next(children)
        except StopIteration:
            stack.pop()
            if identity is not None:
                active.remove(identity)
            continue
        nodes += 1
        if depth > max_depth or nodes > max_nodes:
            raise limit_error_type("value structure exceeds limit")
        if type(item) is str:
            # Encode incrementally instead of allocating a second full text copy.
            for offset in range(0, len(item), 16384):
                try:
                    text_bytes += len(item[offset : offset + 16384].encode("utf-8"))
                except UnicodeEncodeError as exc:
                    raise error_type("invalid Unicode text") from exc
                if text_bytes > max_text_bytes:
                    raise limit_error_type("value text exceeds size limit")
        yield item
        if type(item) in (list, tuple, dict):
            identity = id(item)
            if identity in active:
                raise error_type("cyclic value structure")
            active.add(identity)
            descendants = (
                chain(item.keys(), item.values()) if type(item) is dict else iter(item)
            )
            stack.append((descendants, depth + 1, identity))


def validate_json_values(items: Iterator[Any], error_type: type[RuntimeError]) -> None:
    """Reject JSON coercions that would change RAM values after replay."""
    for item in items:
        if item is None or type(item) in (str, bool, int, list):
            continue
        if type(item) is float and math.isfinite(item):
            continue
        if type(item) is dict and all(type(key) is str for key in item):
            continue
        raise error_type("unsupported JSON value")


def validate_journal_changes(
    changes: list[Any], validate_value: Callable[[Any], None]
) -> list[tuple[str, str, str, Any]]:
    """Validate all operations before the caller publishes any state."""
    validated: list[tuple[str, str, str, Any]] = []
    for change in changes:
        if not isinstance(change, dict):
            raise ValueError("invalid journal change")
        namespace, key = change["namespace"], change["key"]
        if not isinstance(namespace, str) or not isinstance(key, str):
            raise ValueError("invalid journal key")
        operation = change["op"]
        if operation == "set":
            value = change["value"]
            validate_value(value)
            validated.append((operation, namespace, key, value))
        elif operation == "delete":
            validated.append((operation, namespace, key, None))
        else:
            raise ValueError("invalid journal operation")
    return validated
