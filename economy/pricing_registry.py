"""Versioned provider pricing snapshots.

Tariffs are immutable snapshots. Usage records can retain the snapshot metadata
used for a cost calculation so historical trace costs are not silently
recomputed with a newer tariff.
"""

import math
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Optional


@dataclass(frozen=True)
class PricingSnapshot:
    pricing_version: str
    effective_from: str
    effective_to: Optional[str]
    currency: str
    source: str
    source_updated: str
    provider: str
    service: str
    operation: str
    model: Optional[str] = None
    unit: Optional[str] = None
    unit_price: Optional[float] = None

    def __post_init__(self) -> None:
        if self.unit_price is not None and (
            not math.isfinite(self.unit_price) or self.unit_price < 0
        ):
            raise ValueError("unit_price must be a finite non-negative number")
        if self.effective_to is not None and self.effective_to <= self.effective_from:
            raise ValueError("effective_to must be after effective_from")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _windows_overlap(a: "PricingSnapshot", b: "PricingSnapshot") -> bool:
    # Dates are ISO strings, which compare correctly; an open end means "no end".
    a_end = a.effective_to or "9999-12-31"
    b_end = b.effective_to or "9999-12-31"
    return a.effective_from < b_end and b.effective_from < a_end


class PricingRegistry:
    """Registry for immutable pricing snapshots selected by effective date."""

    def __init__(self, snapshots: Optional[Iterable[PricingSnapshot]] = None):
        self._snapshots = list(snapshots or [])

    def register(self, snapshot: PricingSnapshot) -> None:
        if any(
            existing.pricing_version == snapshot.pricing_version
            and existing.provider == snapshot.provider
            and existing.service == snapshot.service
            and existing.operation == snapshot.operation
            and existing.model == snapshot.model
            for existing in self._snapshots
        ):
            raise ValueError("pricing snapshot already registered")
        for existing in self._snapshots:
            same_price_line = (
                existing.provider,
                existing.service,
                existing.operation,
                existing.model,
            ) == (
                snapshot.provider,
                snapshot.service,
                snapshot.operation,
                snapshot.model,
            )
            # An overlapping window would make resolve() pick a tariff by accident.
            if same_price_line and _windows_overlap(existing, snapshot):
                raise ValueError(
                    "pricing snapshot effective window overlaps an existing one"
                )
        self._snapshots.append(snapshot)

    def resolve(
        self,
        *,
        provider: str,
        service: str,
        operation: str,
        effective_date: str,
        model: Optional[str] = None,
    ) -> PricingSnapshot:
        candidates = [
            snapshot
            for snapshot in self._snapshots
            if snapshot.provider == provider
            and snapshot.service == service
            and snapshot.operation == operation
            and snapshot.model == model
            and snapshot.effective_from <= effective_date
            and (
                snapshot.effective_to is None or effective_date < snapshot.effective_to
            )
        ]
        if not candidates:
            raise KeyError("no applicable pricing snapshot")
        return max(candidates, key=lambda snapshot: snapshot.effective_from)

    def snapshots(self) -> list[PricingSnapshot]:
        return list(self._snapshots)


def attach_pricing_snapshot(
    usage: dict[str, Any], snapshot: PricingSnapshot, *, cost: Optional[float] = None
) -> dict[str, Any]:
    """Return a usage record with an immutable pricing snapshot attached."""
    result = dict(usage)
    result["pricing"] = snapshot.to_dict()
    result["cost"] = cost
    return result
