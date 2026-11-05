"""JSON the application speaks: how it is serialized and the ``application/json`` answer."""

from __future__ import annotations

import dataclasses
import decimal
import json
import uuid
from datetime import date
from typing import TYPE_CHECKING, Any

from webframe.http import http_date

if TYPE_CHECKING:
    from webframe.app import Application
    from webframe.wrappers import Response


class JSONProvider:
    """Serializes JSON for ``jsonify`` and reads it from requests.

    Keys are sorted and non-ASCII text is escaped unless a subclass says otherwise;
    dates become HTTP dates, decimals and UUIDs strings, dataclasses dicts.
    An application replaces ``app.json`` with its own provider to change this.
    """

    ensure_ascii = True
    sort_keys = True
    compact: bool | None = None
    mimetype = "application/json"

    def __init__(self, app: Application) -> None:
        self._app = app

    @staticmethod
    def default(obj: Any) -> Any:
        if isinstance(obj, date):
            return http_date(obj)
        if isinstance(obj, (decimal.Decimal, uuid.UUID)):
            return str(obj)
        if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
            return dataclasses.asdict(obj)
        if hasattr(obj, "__html__"):
            return str(obj.__html__())
        raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")

    def dumps(self, obj: Any, **kwargs: Any) -> str:
        kwargs.setdefault("default", self.default)
        kwargs.setdefault("ensure_ascii", self.ensure_ascii)
        kwargs.setdefault("sort_keys", self.sort_keys)
        return json.dumps(obj, **kwargs)

    def loads(self, s: str | bytes, **kwargs: Any) -> Any:
        return json.loads(s, **kwargs)

    def response(self, *args: Any, **kwargs: Any) -> Response:
        """The ``application/json`` response for one value, several (a list) or keywords (a dict)."""
        if args and kwargs:
            raise TypeError("response() takes either positional or keyword arguments")
        if len(args) == 1:
            obj = args[0]
        elif args:
            obj = list(args)
        elif kwargs:
            obj = dict(kwargs)
        else:
            obj = None
        dump_args: dict[str, Any] = {}
        if (self.compact is None and self._app.debug) or self.compact is False:
            dump_args["indent"] = 2
        else:
            dump_args["separators"] = (",", ":")
        return self._app.response_class(
            f"{self.dumps(obj, **dump_args)}\n", mimetype=self.mimetype
        )


__all__ = ["JSONProvider"]
