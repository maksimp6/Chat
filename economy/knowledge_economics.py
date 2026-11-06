"""Semantic economics for knowledge items and agent operations."""

from dataclasses import dataclass
from typing import Any

CURRENCY = "RUB"
PRICING_VERSION = "knowledge-config-v2"


@dataclass(frozen=True)
class KnowledgeCostConfig:
    text_per_kb: float | None = None
    embedding_per_1k_tokens: float | None = None
    vector_storage_per_mb_day: float | None = None
    metadata_per_kb_day: float | None = None
    backup_per_mb_day: float | None = None
    retrieval_per_1k_items: float | None = None


def _cost(rate: float | None, units: float) -> float | None:
    if rate is None:
        return None
    return round(max(0.0, units) * rate, 6)


def calculate_knowledge_cost(
    *,
    text_bytes: int = 0,
    embedding_tokens: int = 0,
    vector_bytes: int = 0,
    metadata_bytes: int = 0,
    backup_bytes: int = 0,
    lifetime_days: float = 0,
    retrieval_count: int = 0,
    config: KnowledgeCostConfig,
) -> dict[str, Any]:
    components = {
        "text": _cost(config.text_per_kb, text_bytes / 1024),
        "embedding": _cost(config.embedding_per_1k_tokens, embedding_tokens / 1000),
        "vector_storage": _cost(
            config.vector_storage_per_mb_day,
            vector_bytes / (1024 * 1024) * max(0.0, lifetime_days),
        ),
        "metadata": _cost(
            config.metadata_per_kb_day, metadata_bytes / 1024 * max(0.0, lifetime_days)
        ),
        "backup": _cost(
            config.backup_per_mb_day,
            backup_bytes / (1024 * 1024) * max(0.0, lifetime_days),
        ),
        "retrieval": _cost(config.retrieval_per_1k_items, retrieval_count / 1000),
    }
    known = [value for value in components.values() if value is not None]
    unknown_components = [name for name, value in components.items() if value is None]
    return {
        "currency": CURRENCY,
        "pricing_version": PRICING_VERSION,
        "components": components,
        "known_cost": round(sum(known), 6),
        "unknown_components": unknown_components,
        "cost_status": "unknown"
        if not known
        else ("partial" if unknown_components else "known"),
    }


def embedding_cost(tokens: int) -> dict[str, Any]:
    """The cost of ``tokens`` of embedding input at the deployment's embedding price.

    ``EMBEDDING_PRICE_PER_1K_TOKENS`` names the price per thousand tokens; without
    it, or with an unreadable or non-finite value, the cost stays unknown: a bad
    price must not turn a paid request into a failure.
    """
    import math
    import os

    price = os.environ.get("EMBEDDING_PRICE_PER_1K_TOKENS")
    try:
        per_1k_tokens = float(price) if price not in (None, "") else None
    except ValueError:
        per_1k_tokens = None
    if per_1k_tokens is not None and not math.isfinite(per_1k_tokens):
        per_1k_tokens = None
    config = KnowledgeCostConfig(embedding_per_1k_tokens=per_1k_tokens)
    return calculate_knowledge_cost(embedding_tokens=tokens, config=config)
