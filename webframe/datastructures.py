"""Containers for HTTP data: headers, multi-valued form data, uploaded files, Accept-*."""

from __future__ import annotations

import io
import os
import shutil
from collections.abc import Callable, Iterable, Iterator, Mapping
from typing import Any, BinaryIO

_missing = object()


def _header_value(value: Any) -> str:
    text = value if isinstance(value, str) else str(value)
    if "\r" in text or "\n" in text:
        raise ValueError("a header value may not contain a line break")
    return text


class Headers:
    """An ordered, case-insensitive multi-value header list.

    ``headers["Content-Type"]`` reads the first value and ``headers["X"] = v``
    replaces every value of that name; ``add`` keeps the ones already there.
    Iterating yields ``(name, value)`` pairs in order.
    """

    __slots__ = ("_list",)

    def __init__(self, defaults: Any = None) -> None:
        self._list: list[tuple[str, str]] = []
        if defaults is not None:
            self.extend(defaults)

    # reading

    def __getitem__(self, key: str) -> str:
        lower = key.lower()
        for name, value in self._list:
            if name.lower() == lower:
                return value
        raise KeyError(key)

    def get(
        self, key: str, default: Any = None, type: Callable[[str], Any] | None = None
    ) -> Any:
        try:
            value = self[key]
        except KeyError:
            return default
        if type is None:
            return value
        try:
            return type(value)
        except ValueError:
            return default

    def getlist(self, key: str) -> list[str]:
        lower = key.lower()
        return [value for name, value in self._list if name.lower() == lower]

    def __contains__(self, key: object) -> bool:
        if not isinstance(key, str):
            return False
        lower = key.lower()
        return any(name.lower() == lower for name, _ in self._list)

    def __iter__(self) -> Iterator[tuple[str, str]]:
        return iter(list(self._list))

    def __len__(self) -> int:
        return len(self._list)

    def __bool__(self) -> bool:
        return bool(self._list)

    def keys(self) -> list[str]:
        return [name for name, _ in self._list]

    def values(self) -> list[str]:
        return [value for _, value in self._list]

    def items(self) -> list[tuple[str, str]]:
        return list(self._list)

    def to_wsgi_list(self) -> list[tuple[str, str]]:
        return list(self._list)

    def copy(self) -> Headers:
        return Headers(self._list)

    # writing

    def add(self, key: str, value: Any) -> None:
        self._list.append((key, _header_value(value)))

    def set(self, key: str, value: Any) -> None:
        text = _header_value(value)
        lower = key.lower()
        kept: list[tuple[str, str]] = []
        replaced = False
        for name, old in self._list:
            if name.lower() != lower:
                kept.append((name, old))
            elif not replaced:
                kept.append((key, text))
                replaced = True
        if not replaced:
            kept.append((key, text))
        self._list = kept

    __setitem__ = set

    def setdefault(self, key: str, default: Any) -> str:
        try:
            return self[key]
        except KeyError:
            self.set(key, default)
            return self[key]

    def remove(self, key: str) -> None:
        lower = key.lower()
        self._list = [
            (name, value) for name, value in self._list if name.lower() != lower
        ]

    def __delitem__(self, key: str) -> None:
        if key not in self:
            raise KeyError(key)
        self.remove(key)

    def pop(self, key: str, default: Any = _missing) -> Any:
        try:
            value = self[key]
        except KeyError:
            if default is _missing:
                raise
            return default
        self.remove(key)
        return value

    def extend(self, iterable: Any) -> None:
        for key, value in _pairs(iterable):
            self.add(key, value)

    def update(self, iterable: Any = None, **kwargs: Any) -> None:
        if iterable is not None:
            for key, value in _pairs(iterable):
                self.set(key, value)
        for key, value in kwargs.items():
            self.set(key, value)

    def clear(self) -> None:
        self._list = []

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Headers):
            return NotImplemented
        return sorted((k.lower(), v) for k, v in self._list) == sorted(
            (k.lower(), v) for k, v in other._list
        )

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._list!r})"


def _pairs(iterable: Any) -> Iterable[tuple[str, Any]]:
    if isinstance(iterable, Headers):
        return iterable.items()
    if isinstance(iterable, Mapping):
        return iterable.items()
    if hasattr(iterable, "items") and callable(iterable.items):
        return iterable.items()
    return iterable


class MultiDict:
    """A mapping whose keys may carry several values (query strings, forms).

    ``d[key]`` and ``d.get(key)`` return the first value; ``getlist`` returns
    all of them. ``get(key, default, type=int)`` converts and falls back to
    ``default`` when the conversion fails, as the query parsers expect.
    """

    __slots__ = ("_dict",)

    def __init__(self, mapping: Any = None) -> None:
        self._dict: dict[str, list[Any]] = {}
        if mapping is None:
            return
        if isinstance(mapping, MultiDict):
            for key, values in mapping.lists():
                self._dict[key] = list(values)
        elif isinstance(mapping, Mapping):
            for key, value in mapping.items():
                if isinstance(value, (list, tuple)):
                    self._dict[key] = list(value)
                else:
                    self._dict[key] = [value]
        else:
            for key, value in mapping:
                self._dict.setdefault(key, []).append(value)

    def __getitem__(self, key: str) -> Any:
        try:
            return self._dict[key][0]
        except (KeyError, IndexError):
            from webframe.exceptions import BadRequestKeyError

            raise BadRequestKeyError(key) from None

    def get(
        self, key: str, default: Any = None, type: Callable[[Any], Any] | None = None
    ) -> Any:
        values = self._dict.get(key)
        if not values:
            return default
        value = values[0]
        if type is None:
            return value
        try:
            return type(value)
        except (ValueError, TypeError):
            return default

    def getlist(self, key: str, type: Callable[[Any], Any] | None = None) -> list[Any]:
        values = self._dict.get(key, [])
        if type is None:
            return list(values)
        out = []
        for value in values:
            try:
                out.append(type(value))
            except (ValueError, TypeError):
                continue
        return out

    def add(self, key: str, value: Any) -> None:
        self._dict.setdefault(key, []).append(value)

    def setlist(self, key: str, values: Iterable[Any]) -> None:
        self._dict[key] = list(values)

    def __setitem__(self, key: str, value: Any) -> None:
        self._dict[key] = [value]

    def __delitem__(self, key: str) -> None:
        del self._dict[key]

    def __contains__(self, key: object) -> bool:
        return key in self._dict and bool(self._dict[key])

    def __iter__(self) -> Iterator[str]:
        return iter(self._dict)

    def __len__(self) -> int:
        return len(self._dict)

    def __bool__(self) -> bool:
        return any(self._dict.values())

    def keys(self) -> list[str]:
        return list(self._dict)

    def values(self) -> list[Any]:
        return [values[0] for values in self._dict.values() if values]

    def items(self, multi: bool = False) -> list[tuple[str, Any]]:
        if multi:
            return [
                (key, value) for key, values in self._dict.items() for value in values
            ]
        return [(key, values[0]) for key, values in self._dict.items() if values]

    def lists(self) -> list[tuple[str, list[Any]]]:
        return [(key, list(values)) for key, values in self._dict.items()]

    def to_dict(self, flat: bool = True) -> dict[str, Any]:
        if flat:
            return dict(self.items())
        return {key: list(values) for key, values in self._dict.items()}

    def copy(self) -> MultiDict:
        return MultiDict(self)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, MultiDict):
            return NotImplemented
        return self._dict == other._dict

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.items(multi=True)!r})"


class FileStorage:
    """One uploaded file of a multipart form: its field name, file name and stream."""

    def __init__(
        self,
        stream: BinaryIO | None = None,
        filename: str | None = None,
        name: str | None = None,
        content_type: str | None = None,
        headers: Headers | None = None,
    ) -> None:
        self.name = name
        self.stream = stream if stream is not None else io.BytesIO()
        self.filename = filename
        self.headers = headers if headers is not None else Headers()
        if content_type is not None:
            self.headers["Content-Type"] = content_type

    @property
    def content_type(self) -> str | None:
        return self.headers.get("Content-Type")

    @property
    def mimetype(self) -> str:
        content_type = self.content_type or ""
        return content_type.split(";", 1)[0].strip().lower()

    @property
    def content_length(self) -> int:
        return self.headers.get("Content-Length", 0, type=int) or 0

    def read(self, size: int = -1) -> bytes:
        return self.stream.read(size)

    def save(self, dst: Any, buffer_size: int = 16384) -> None:
        close = False
        if isinstance(dst, (str, bytes, os.PathLike)):
            dst = open(dst, "wb")
            close = True
        try:
            shutil.copyfileobj(self.stream, dst, buffer_size)
        finally:
            if close:
                dst.close()

    def close(self) -> None:
        try:
            self.stream.close()
        except Exception:
            pass

    def __bool__(self) -> bool:
        return bool(self.filename)

    def __iter__(self) -> Iterator[bytes]:
        return iter(self.stream)

    def __repr__(self) -> str:
        return f"<{type(self).__name__}: {self.filename!r} ({self.content_type!r})>"


class Accept:
    """A parsed ``Accept-*`` header: ``accept["gzip"]`` is a value's quality, 0 when refused."""

    def __init__(self, header: str | None) -> None:
        self.values: list[tuple[str, float]] = []
        for item in (header or "").split(","):
            item = item.strip()
            if not item:
                continue
            value, _, params = item.partition(";")
            quality = 1.0
            for param in params.split(";"):
                name, _, raw = param.strip().partition("=")
                if name.strip().lower() == "q":
                    try:
                        quality = max(0.0, min(1.0, float(raw.strip())))
                    except ValueError:
                        quality = 0.0
            self.values.append((value.strip(), quality))

    def __getitem__(self, key: str) -> float:
        lower = key.lower()
        for value, quality in self.values:
            if value.lower() == lower:
                return quality
        for value, quality in self.values:
            if value == "*":
                return quality
        return 0.0

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and self[key] > 0

    def __iter__(self) -> Iterator[tuple[str, float]]:
        return iter(self.values)

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.values!r})"


__all__ = ["Accept", "FileStorage", "Headers", "MultiDict"]
