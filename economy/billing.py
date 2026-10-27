"""Billing helpers for ExecutionTrace.

Pricing is intentionally read from the existing model configuration. Unknown
models never become a confirmed zero-cost invocation.
"""

from typing import Any, Optional

from core.config import (
    ALL_MODELS,
    AUDIO_STT_PRICE_PER_SEC,
    AUDIO_TTS_PRICE_PER_SEC,
    canonical_model_key,
)
from core.wikifunctions import trim_string

PRICING_CURRENCY = "RUB"
PRICING_VERSION = "config-v1"
PROVIDER = "yandex_ai_studio"


def extract_cached_tokens(usage: dict[str, Any]) -> int:
    # The raw provider usage nests the count in the input details; the normalized usage that the
    # parser returns carries it at the top level. Both are read, so a cached input is never billed
    # at the full price.
    details = usage.get("input_token_details") or usage.get("input_tokens_details") or {}
    value = details.get("cached_tokens") if isinstance(details, dict) else None
    if value is None:
        value = usage.get("cached_tokens")
    return int(value or 0)


def build_ai_billing_item(
    model_key: Optional[str],
    usage: Optional[dict[str, Any]],
    step: int,
    response_id: Optional[str] = None,
) -> dict[str, Any]:
    usage_missing = not isinstance(usage, dict)
    usage = {} if usage_missing else usage
    input_tokens = int(usage.get("input_tokens", 0) or 0)
    output_tokens = int(usage.get("output_tokens", 0) or 0)
    total_tokens = int(usage.get("total_tokens", input_tokens + output_tokens) or 0)
    cached_tokens = min(max(extract_cached_tokens(usage), 0), max(input_tokens, 0))
    audio_stt = float(usage.get("audio_seconds_stt", 0) or 0)
    audio_tts = float(usage.get("audio_seconds_tts", 0) or 0)

    item: dict[str, Any] = {
        "type": "ai",
        "step": step,
        "provider": PROVIDER,
        "model": model_key,
        "currency": PRICING_CURRENCY,
        "pricing_version": PRICING_VERSION,
        "response_id": response_id,
        "input_tokens": input_tokens,
        "cached_input_tokens": cached_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }
    # A retired model keeps its own prices for accounting; only legacy keys resolve to a successor.
    pricing = (ALL_MODELS.get(model_key) or ALL_MODELS.get(canonical_model_key(model_key))) if model_key else None
    if usage_missing or pricing is None or pricing.get("pricing_status") == "unknown":
        item.update(
            {
                "cost_status": "unknown",
                "cost_reason": "usage_missing" if usage_missing else "pricing_not_configured",
                "input_cost": None,
                "output_cost": None,
                "cached_input_cost": None,
                "audio_cost": None,
                "total_cost": None,
                "cache_savings": None,
            }
        )
        return item
    billable_input = max(input_tokens - cached_tokens, 0)
    input_cost = billable_input * pricing["input"] / 1000
    cached_input_cost = cached_tokens * pricing.get("cached", pricing["input"]) / 1000
    output_cost = output_tokens * pricing.get("output", pricing["input"]) / 1000
    audio_cost = audio_stt * AUDIO_STT_PRICE_PER_SEC + audio_tts * AUDIO_TTS_PRICE_PER_SEC
    cache_savings = (
        cached_tokens * max(pricing["input"] - pricing.get("cached", pricing["input"]), 0) / 1000
    )
    total_cost = input_cost + cached_input_cost + output_cost + audio_cost
    item.update(
        {
            "cost_status": "calculated",
            "input_cost": round(input_cost, 6),
            "output_cost": round(output_cost, 6),
            "cached_input_cost": round(cached_input_cost, 6),
            "audio_cost": round(audio_cost, 6),
            "cache_savings": round(cache_savings, 6),
            "total_cost": round(total_cost, 6),
        }
    )
    return item


def aggregate_billing(items, context: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    known = [item for item in items if item.get("cost_status") == "calculated"]
    total = round(sum(float(item.get("total_cost") or 0) for item in known), 6)
    input_cost = round(sum(float(item.get("input_cost") or 0) for item in known), 6)
    output_cost = round(sum(float(item.get("output_cost") or 0) for item in known), 6)
    cached_cost = round(sum(float(item.get("cached_input_cost") or 0) for item in known), 6)
    cache_savings = round(sum(float(item.get("cache_savings") or 0) for item in known), 6)
    unknown = sum(1 for item in items if item.get("cost_status") == "unknown")
    compute = [item for item in items if item.get("type") == "compute"]
    compute_cost = round(
        sum(
            float(item.get("total_cost") or 0)
            for item in compute
            if item.get("cost_status") == "calculated"
        ),
        6,
    )
    result = {
        "currency": PRICING_CURRENCY,
        "provider": PROVIDER,
        "pricing_version": PRICING_VERSION,
        "input_tokens": sum(int(item.get("input_tokens") or 0) for item in items),
        "output_tokens": sum(int(item.get("output_tokens") or 0) for item in items),
        "cached_input_tokens": sum(int(item.get("cached_input_tokens") or 0) for item in items),
        "total_tokens": sum(int(item.get("total_tokens") or 0) for item in items),
        "input_cost": input_cost,
        "output_cost": output_cost,
        "cached_input_cost": cached_cost,
        "tool_cost": 0.0,
        "compute_cost": compute_cost,
        "cpu_seconds": round(sum(float(item.get("cpu_seconds") or 0) for item in compute), 6),
        "energy_wh": round(sum(float(item.get("energy_wh") or 0) for item in compute), 9),
        "total_cost": total,
        "cache_savings": cache_savings,
        "cost_status": "partial" if unknown else "calculated",
        "unknown_cost_items": unknown,
        "items": items,
    }
    # With no priced model call, a sum of zero is not a cost: the total stays unknown.
    if unknown and not any(item.get("type") == "ai" for item in known):
        result["total_cost"] = None
    if context:
        for key in ("invocation_id", "session_id", "conversation_id", "trace_id", "owner_id"):
            if context.get(key) is not None:
                result[key] = str(context[key])
    return result


def settle_billing_to_treasury(billing: dict[str, Any], owner_id: Optional[str]) -> dict[str, Any]:
    """Post a fully calculated trace billing aggregate exactly once."""
    if not isinstance(billing, dict) or billing.get("cost_status") != "calculated":
        return {"status": "skipped", "reason": "billing_not_fully_calculated"}

    trusted_owner_id = trim_string(str(owner_id)) if owner_id else ""
    if not trusted_owner_id:
        return {"status": "skipped", "reason": "owner_identity_missing"}

    billing_owner = billing.get("owner_id")
    normalized_billing_owner = (
        trim_string(str(billing_owner)) if billing_owner is not None else ""
    )
    if not normalized_billing_owner:
        billing["owner_id"] = trusted_owner_id
        normalized_billing_owner = trusted_owner_id
    if normalized_billing_owner != trusted_owner_id:
        raise ValueError("billing owner identity does not match the invocation owner")

    amount = float(billing.get("total_cost") or 0)
    if amount <= 0:
        return {"status": "skipped", "reason": "zero_cost"}

    import math

    if not math.isfinite(amount):
        raise ValueError("billing amount must be finite")

    trace_key = billing.get("trace_id") or billing.get("invocation_id")
    if not trace_key:
        return {"status": "skipped", "reason": "billing_reference_missing"}

    reference = "billing:" + str(trace_key)
    from economy.treasury import post_expense

    created = post_expense(trusted_owner_id, amount, "AI usage", reference)
    return {
        "status": "posted" if created else "already_posted",
        "amount": amount,
        "reference": reference,
        "owner_id": trusted_owner_id,
    }
