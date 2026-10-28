"""Semantic economics for knowledge items and agent operations."""

from dataclasses import dataclass
from typing import Any, Optional

CURRENCY = "RUB"
PRICING_VERSION = "knowledge-config-v2"
OBJECT_STORAGE_PRICING_SOURCE = "https://yandex.cloud/ru/docs/storage/pricing"
OBJECT_STORAGE_PRICING_UPDATED = "2026-06-30"


@dataclass(frozen=True)
class KnowledgeCostConfig:
    text_per_kb: Optional[float] = None
    embedding_per_1k_tokens: Optional[float] = None
    vector_storage_per_mb_day: Optional[float] = None
    metadata_per_kb_day: Optional[float] = None
    backup_per_mb_day: Optional[float] = None
    retrieval_per_1k_items: Optional[float] = None


@dataclass(frozen=True)
class ProviderStorageTariff:
    provider: str
    service: str
    region: str
    storage_per_gb_month: float
    free_storage_gb_month: float = 0.0
    get_per_10k: Optional[float] = None
    free_get_per_month: int = 0
    write_per_10k: Optional[float] = None
    free_write_per_month: int = 0
    currency: str = CURRENCY
    pricing_version: str = PRICING_VERSION
    source: str = OBJECT_STORAGE_PRICING_SOURCE
    source_updated: str = OBJECT_STORAGE_PRICING_UPDATED


YANDEX_OBJECT_STORAGE_STANDARD_RU = ProviderStorageTariff(
    provider="yandex_cloud",
    service="object_storage",
    region="ru",
    storage_per_gb_month=2.376,
    free_storage_gb_month=1.0,
    get_per_10k=0.46,
    free_get_per_month=100_000,
    write_per_10k=None,
    free_write_per_month=10_000,
)


def _cost(rate: Optional[float], units: float) -> Optional[float]:
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
            config.vector_storage_per_mb_day, vector_bytes / (1024 * 1024) * max(0.0, lifetime_days)
        ),
        "metadata": _cost(
            config.metadata_per_kb_day, metadata_bytes / 1024 * max(0.0, lifetime_days)
        ),
        "backup": _cost(
            config.backup_per_mb_day, backup_bytes / (1024 * 1024) * max(0.0, lifetime_days)
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
        "cost_status": "unknown" if not known else ("partial" if unknown_components else "known"),
    }


@dataclass(frozen=True)
class Policy:
    policy_id: str
    currency: str = CURRENCY
    reward_value_threshold: float = 1.0
    penalty_cost_ratio: float = 2.0
    max_penalty: float = 0.0

