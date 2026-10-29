"""Pagination of tool text, argument helpers, filters and element-ref parsing.

Content is never cut silently: ``paginate`` returns one window of the text and
the ``next_offset`` to continue from, and ``max_chars`` 0 means everything.
"""

from __future__ import annotations

import logging
import math
import re
from typing import Any, Callable, Mapping

from browser.errors import (
    E_ARG_NEGATIVE,
    E_ARG_TYPE,
    E_OFFSET,
    E_REGEX,
    BrowserError,
)

logger = logging.getLogger("logos.browser.paging")

_REF_PATTERN = re.compile(r"^\[?\s*([LBIFTMN])\s*(\d+)\s*\]?$", re.I)
_TRUE_WORDS = frozenset({"true", "1", "yes", "on", "да"})
_FALSE_WORDS = frozenset({"false", "0", "no", "off", "нет"})


def paginate(text: str, offset: int | None, max_chars: int | None) -> dict[str, Any]:
    """One window of ``text``: content, offset, next_offset and total_chars.

    ``max_chars`` None or 0 returns everything from ``offset``. When the window
    ends before the text does, the end moves back to the last line break in the
    second half of the window, so lines are not split.
    """
    text = text or ""
    total = len(text)
    start = 0 if offset is None else int(offset)
    if start < 0:
        raise BrowserError(E_ARG_NEGATIVE.format(name="offset", value=start))
    limit = 0 if max_chars is None else int(max_chars)
    if limit < 0:
        raise BrowserError(E_ARG_NEGATIVE.format(name="max_chars", value=limit))
    if start > total:
        raise BrowserError(E_OFFSET.format(offset=start, total=total))
    if start == total:
        return {"content": "", "offset": start, "next_offset": None, "total_chars": total}
    end = total if limit == 0 else min(total, start + limit)
    if end < total:
        floor = start + limit // 2
        newline = text.rfind("\n", floor, end)
        if newline >= 0 and newline + 1 > start:
            end = newline + 1
    return {
        "content": text[start:end],
        "offset": start,
        "next_offset": end if end < total else None,
        "total_chars": total,
    }


# ---------------------------------------------------------------------------
# Argument helpers: a missing argument and None are the same.


def _describe(value: Any) -> str:
    text = repr(value)
    return text if len(text) <= 80 else text[:79] + "…"


def arg_str(args: Mapping[str, Any] | None, name: str) -> str | None:
    """A stripped string argument; empty or missing gives None."""
    value = (args or {}).get(name)
    if value is None:
        return None
    text = (value if isinstance(value, str) else str(value)).strip()
    return text or None


def arg_raw_str(args: Mapping[str, Any] | None, name: str) -> str | None:
    """A string argument kept exactly as given (values, typed text)."""
    value = (args or {}).get(name)
    if value is None:
        return None
    return value if isinstance(value, str) else str(value)


def arg_int(args: Mapping[str, Any] | None, name: str) -> int | None:
    value = (args or {}).get(name)
    if value is None:
        return None
    if isinstance(value, bool):
        raise BrowserError(
            E_ARG_TYPE.format(name=name, expected="целым числом", value=_describe(value))
        )
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            pass
    raise BrowserError(
        E_ARG_TYPE.format(name=name, expected="целым числом", value=_describe(value))
    )


def arg_float(args: Mapping[str, Any] | None, name: str) -> float | None:
    value = (args or {}).get(name)
    if value is None:
        return None
    number: float | None = None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip().replace(",", "."))
        except ValueError:
            number = None
    if number is None or not math.isfinite(number):
        raise BrowserError(
            E_ARG_TYPE.format(name=name, expected="конечным числом", value=_describe(value))
        )
    return number


def arg_bool(args: Mapping[str, Any] | None, name: str, absent: bool) -> bool:
    value = (args or {}).get(name)
    if value is None:
        return absent
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        word = value.strip().lower()
        if word in _TRUE_WORDS:
            return True
        if word in _FALSE_WORDS:
            return False
    raise BrowserError(
        E_ARG_TYPE.format(name=name, expected="true или false", value=_describe(value))
    )


def arg_list(args: Mapping[str, Any] | None, name: str) -> list[Any] | None:
    """A list argument; missing gives None, a single scalar becomes [value]."""
    value = (args or {}).get(name)
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def arg_pairs(args: Mapping[str, Any] | None, name: str) -> list[tuple[str, str]]:
    """Name/value pairs from [{name, value}] (also [[name, value]] or a mapping)."""
    value = (args or {}).get(name)
    if value is None:
        return []
    items: list[Any]
    if isinstance(value, Mapping):
        items = [{"name": key, "value": item} for key, item in value.items()]
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        raise BrowserError(
            E_ARG_TYPE.format(
                name=name, expected="списком пар {name, value}", value=_describe(value)
            )
        )
    pairs: list[tuple[str, str]] = []
    for item in items:
        if isinstance(item, Mapping):
            key, raw = item.get("name"), item.get("value")
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            key, raw = item[0], item[1]
        else:
            raise BrowserError(
                E_ARG_TYPE.format(
                    name=name, expected="списком пар {name, value}", value=_describe(item)
                )
            )
        key_text = "" if key is None else str(key).strip()
        if not key_text:
            raise BrowserError(
                E_ARG_TYPE.format(
                    name=f"{name}[].name", expected="непустой строкой", value=_describe(key)
                )
            )
        pairs.append((key_text, "" if raw is None else str(raw)))
    return pairs


# ---------------------------------------------------------------------------
# Filters, refs, sizes


def compile_filter(pattern: str | None) -> Callable[[str], bool]:
    """A line filter: "/regex/" (case-insensitive) or a casefold substring."""
    if pattern is None or pattern == "":
        return lambda line: True
    if len(pattern) >= 2 and pattern.startswith("/") and pattern.endswith("/"):
        inner = pattern[1:-1]
        try:
            search = re.compile(inner, re.I).search
        except re.error as exc:
            raise BrowserError(E_REGEX.format(q=pattern, reason=str(exc))) from exc
        return lambda line: search(line) is not None
    needle = pattern.casefold()
    return lambda line: needle in line.casefold()


def parse_ref(text: Any) -> tuple[str, int] | None:
    """("L", 5) for "L5", "[L5]", "l5" or "[ L5 ]"; None for anything else."""
    if text is None:
        return None
    match = _REF_PATTERN.match(str(text).strip())
    if match is None:
        return None
    return match.group(1).upper(), int(match.group(2))


def normalize_ref(text: Any) -> str:
    """The canonical spelling "L5"; text that is not a ref comes back stripped."""
    parsed = parse_ref(text)
    if parsed is None:
        return str(text or "").strip()
    return f"{parsed[0]}{parsed[1]}"


def human_bytes(n: int | None) -> str:
    """"512 Б", "1,2 КБ", "3,4 МБ", "1,1 ГБ"; None gives "размер неизвестен"."""
    if n is None:
        return "размер неизвестен"
    size = int(n)
    sign = "-" if size < 0 else ""
    size = abs(size)
    if size < 1024:
        return f"{sign}{size} Б"
    value = float(size)
    for unit in ("КБ", "МБ", "ГБ", "ТБ"):
        value /= 1024
        if value < 1024 or unit == "ТБ":
            return f"{sign}{value:.1f} {unit}".replace(".", ",")
    return f"{sign}{size} Б"


def plural(n: int, one: str, few: str, many: str) -> str:
    """Russian plural form for ``n``: 1 ссылка, 2 ссылки, 5 ссылок."""
    value = abs(int(n)) % 100
    if 11 <= value <= 14:
        return many
    last = value % 10
    if last == 1:
        return one
    if 2 <= last <= 4:
        return few
    return many


__all__ = [
    "arg_bool",
    "arg_float",
    "arg_int",
    "arg_list",
    "arg_pairs",
    "arg_raw_str",
    "arg_str",
    "compile_filter",
    "human_bytes",
    "normalize_ref",
    "paginate",
    "parse_ref",
    "plural",
]
