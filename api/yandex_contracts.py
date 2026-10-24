"""Offline payload validation for the pinned Yandex documentation snapshot.

This registry is intentionally separate from internal runtime API contracts.
It does not make a request, enable an unsupported API, or claim live conformance.
"""

from __future__ import annotations

import json
import math
import re
from copy import deepcopy
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft7Validator, FormatChecker

from api.contracts import ContractViolation

CATALOG = Path(__file__).resolve().parents[1] / "contracts/yandex/catalog.json"
FORMATS = FormatChecker()


@FORMATS.checks("date-time")
def _date_time(value: Any) -> bool:
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})", value
    ):
        return False
    try:
        return datetime.fromisoformat(value).tzinfo is not None
    except ValueError:
        return False


@FORMATS.checks("int64")
def _int64(value: Any) -> bool:
    if isinstance(value, str):
        return (
            len(value) <= 20
            and bool(re.fullmatch(r"-?(?:0|[1-9][0-9]*)", value))
            and -(2**63) <= int(value) < 2**63
        )
    return isinstance(value, int) and not isinstance(value, bool) and -(2**63) <= value < 2**63


@FORMATS.checks("double")
def _double(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and -1.7976931348623157e308 <= value <= 1.7976931348623157e308
    )


class IncompleteYandexContract(ContractViolation):
    """The source does not establish an executable constraint; never a PASS."""


@lru_cache(maxsize=1)
def _catalog() -> dict[str, dict[str, Any]]:
    from api.yandex_request_profiles import PROFILES

    data = json.loads(CATALOG.read_text())
    return {**{entry["name"]: entry for entry in data["contracts"]}, **PROFILES}


def get_yandex_contract(name: str) -> dict[str, Any]:
    """Return an isolated copy: callers cannot mutate the shared registry."""
    try:
        return deepcopy(_catalog()[name])
    except KeyError as exc:
        raise ContractViolation("unknown Yandex contract: " + name) from exc


def schema_gaps(document: dict[str, Any], reference: str) -> list[str]:
    """Inspect only reachable schemas. Never fetch an external reference."""
    gaps: list[str] = []
    visited: set[str] = set()

    def visit(spec: Any, path: str) -> None:
        if isinstance(spec, bool):
            return
        if not isinstance(spec, dict):
            gaps.append(path + ": invalid schema")
            return
        for key in ("x-unresolved", "x-prose-constraints"):
            if key in spec:
                gaps.append(path + ": " + str(spec[key]))
        if "format" in spec and spec["format"] not in FORMATS.checkers:
            gaps.append(path + ": unresolved format " + spec["format"])
        if "$ref" in spec:
            ref = spec["$ref"]
            if ref in visited:
                return
            visited.add(ref)
            prefix = "#/definitions/"
            if not isinstance(ref, str) or not ref.startswith(prefix):
                gaps.append(path + ": external/unsupported reference")
                return
            name = ref[len(prefix) :].replace("~1", "/").replace("~0", "~")
            if name not in document.get("definitions", {}):
                gaps.append(path + ": missing definition " + name)
            else:
                visit(document["definitions"][name], ref)
        for key in ("properties", "patternProperties"):
            for name, child in spec.get(key, {}).items():
                visit(child, path + "/" + key + "/" + name)
        for key in ("items", "additionalProperties", "propertyNames", "not"):
            if key in spec:
                visit(spec[key], path + "/" + key)
        for key in ("anyOf", "allOf", "oneOf"):
            for index, child in enumerate(spec.get(key, [])):
                visit(child, path + "/" + key + "/" + str(index))

    visit({"$ref": reference}, "$")
    return sorted(set(gaps))


def _require_json(value: Any) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float) and math.isfinite(value):
        return
    if isinstance(value, list):
        for item in value:
            _require_json(item)
        return
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        for item in value.values():
            _require_json(item)
        return
    raise ContractViolation("Yandex payload must contain only finite JSON values")


def validate_yandex_contract[PayloadT](
    name: str, payload: PayloadT, *, direction: str = "request", part: str = "body"
) -> PayloadT:
    """Validate one named JSON body, path/query mapping, or decoded gRPC message.

    For responses pass direction='responses', part='200' (or 'message' for gRPC).
    Multipart/binary, unknown types, missing schemas and uncompiled constraints
    raise IncompleteYandexContract, including in optional or union branches.
    """
    contract = get_yandex_contract(name)
    if contract["availability"] == "unsupported_in_snapshot":
        raise IncompleteYandexContract("API/event is marked unsupported in this snapshot")
    if direction not in ("request", "responses"):
        raise ContractViolation("direction must be request or responses")
    reference = contract[direction].get(part)
    if reference is None:
        raise IncompleteYandexContract("no documented schema for " + direction + "." + part)
    document = contract["schema"]
    gaps = schema_gaps(document, reference)
    if gaps:
        # Do not echo payloads, arguments, credentials, or potentially sensitive values.
        raise IncompleteYandexContract("source schema is incomplete: " + gaps[0])
    _require_json(payload)
    schema = {**document, "$ref": reference}
    error = next(Draft7Validator(schema, format_checker=FORMATS).iter_errors(payload), None)
    if error is not None:
        # jsonschema's default message includes the rejected value: do not expose it.
        raise ContractViolation(
            "Yandex payload violates " + name + " (" + str(error.validator) + ")"
        )
    if name.startswith("yandex.runtime.") and part == "body" and isinstance(payload, dict):
        chunking = payload.get("chunking_strategy")
        if isinstance(chunking, dict) and chunking.get("type") == "static":
            static = chunking["static"]
            if static["chunk_overlap_tokens"] * 2 > static["max_chunk_size_tokens"]:
                raise ContractViolation("chunk overlap exceeds half the chunk size")
    return payload
