"""Money and numeric-field helpers shared by 3D-printing quote, finance and service code."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Literal, overload


def money_float(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


@overload
def decimal_field(
    value: Any, name: str, *, minimum: str = "0", nullable: Literal[False] = False
) -> Decimal: ...


@overload
def decimal_field(
    value: Any, name: str, *, minimum: str = "0", nullable: Literal[True]
) -> Decimal | None: ...


def decimal_field(
    value: Any, name: str, *, minimum: str = "0", nullable: bool = False
) -> Decimal | None:
    if value is None and nullable:
        return None
    try:
        number = Decimal(str(value))
    except Exception as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not number.is_finite():
        raise ValueError(f"{name} must be a finite number")
    if number < Decimal(minimum):
        raise ValueError(f"{name} must be >= {minimum}")
    return number
