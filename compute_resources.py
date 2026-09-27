"""Energy-based compute billing for ExecutionTrace.

Only CPU time actually consumed by the invocation's thread is billed. Time
spent waiting on the model provider, network or sleep is not local compute
and is already paid for through provider token pricing.

    energy_wh = cpu_seconds * watts_per_core / 3600
    cost      = energy_wh / 1000 * price_per_kwh
"""

import logging
import math
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

PRICING_CURRENCY = "RUB"
PRICING_VERSION = "energy-v1"
PROVIDER = "alice_compute"
DEFAULT_CPU_WATTS_PER_CORE = 15.0


def _positive_env_float(name: str) -> Optional[float]:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        logger.warning("%s is not a number; ignoring it", name)
        return None
    if not math.isfinite(value) or value < 0:
        logger.warning("%s must be a finite non-negative number; ignoring it", name)
        return None
    return value


def get_energy_pricing() -> Dict[str, Optional[float]]:
    watts = _positive_env_float("ALICE_CPU_WATTS_PER_CORE")
    return {
        "watts_per_core": DEFAULT_CPU_WATTS_PER_CORE if watts is None else watts,
        "price_per_kwh": _positive_env_float("ALICE_ELECTRICITY_PRICE_RUB_PER_KWH"),
    }


def build_compute_billing_item(
    cpu_seconds: Optional[float],
    wall_seconds: Optional[float] = None,
    pricing: Optional[Dict[str, Optional[float]]] = None,
) -> Dict[str, Any]:
    pricing = pricing or get_energy_pricing()
    watts = float(pricing["watts_per_core"])
    price = pricing.get("price_per_kwh")
    item: Dict[str, Any] = {
        "type": "compute",
        "provider": PROVIDER,
        "currency": PRICING_CURRENCY,
        "pricing_version": PRICING_VERSION,
        "watts_per_core": watts,
        "price_per_kwh": price,
        "wall_seconds": None if wall_seconds is None else round(wall_seconds, 6),
        "cpu_seconds": None,
        "cpu_utilization": None,
        "energy_wh": None,
        "energy_joules": None,
        "total_cost": 0.0,
    }
    if cpu_seconds is None or not math.isfinite(cpu_seconds) or cpu_seconds < 0:
        item.update({"cost_status": "not_billed", "cost_reason": "cpu_time_unmeasured"})
        return item

    energy_joules = cpu_seconds * watts
    energy_wh = energy_joules / 3600
    item.update(
        {
            "cpu_seconds": round(cpu_seconds, 6),
            "energy_joules": round(energy_joules, 6),
            "energy_wh": round(energy_wh, 9),
        }
    )
    if wall_seconds:
        item["cpu_utilization"] = round(min(cpu_seconds / wall_seconds, 1.0), 4)
    if price is None:
        item.update(
            {"cost_status": "not_billed", "cost_reason": "electricity_price_not_configured"}
        )
        return item
    item.update(
        {
            "cost_status": "calculated",
            "total_cost": round(energy_wh / 1000 * price, 6),
        }
    )
    return item
