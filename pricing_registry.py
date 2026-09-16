"""Versioned provider pricing snapshots.

Tariffs are immutable snapshots. Usage records can retain the snapshot metadata
used for a cost calculation so historical trace costs are not silently
recomputed with a newer tariff.
"""
from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, Optional


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

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class PricingRegistry:
    """Registry for immutable pricing snapshots selected by effective date."""

    def __init__(self, snapshots: Optional[Iterable[PricingSnapshot]] = None):
        self._snapshots = list(snapshots or [])

    def register(self, snapshot: PricingSnapshot) -> None:
        if any(existing.pricing_version == snapshot.pricing_version
               and existing.provider == snapshot.provider
               and existing.service == snapshot.service
               and existing.operation == snapshot.operation
               and existing.model == snapshot.model
               for existing in self._snapshots):
            raise ValueError("pricing snapshot already registered")
        self._snapshots.append(snapshot)

    def resolve(self, *, provider: str, service: str, operation: str,
                effective_date: str, model: Optional[str] = None) -> PricingSnapshot:
        candidates = [snapshot for snapshot in self._snapshots
                      if snapshot.provider == provider
                      and snapshot.service == service
                      and snapshot.operation == operation
                      and snapshot.model == model
                      and snapshot.effective_from <= effective_date
                      and (snapshot.effective_to is None or effective_date < snapshot.effective_to)]
        if not candidates:
            raise KeyError("no applicable pricing snapshot")
        return max(candidates, key=lambda snapshot: snapshot.effective_from)

    def snapshots(self) -> list[PricingSnapshot]:
        return list(self._snapshots)


def attach_pricing_snapshot(usage: Dict[str, Any], snapshot: PricingSnapshot,
                            *, cost: Optional[float] = None) -> Dict[str, Any]:
    """Return a usage record with an immutable pricing snapshot attached."""
    result = dict(usage)
    result["pricing"] = snapshot.to_dict()
    result["cost"] = cost
    return result
