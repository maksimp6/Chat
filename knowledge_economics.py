"""Semantic economics for knowledge items and agent operations."""
from dataclasses import dataclass
from typing import Any, Dict, Optional

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
    provider="yandex_cloud", service="object_storage", region="ru",
    storage_per_gb_month=2.376, free_storage_gb_month=1.0,
    get_per_10k=0.46, free_get_per_month=100_000,
    write_per_10k=None, free_write_per_month=10_000,
)

def _cost(rate: Optional[float], units: float) -> Optional[float]:
    if rate is None:
        return None
    return round(max(0.0, units) * rate, 6)

def calculate_provider_storage_cost(*, bytes_stored: int, lifetime_days: float,
                                    tariff: ProviderStorageTariff, days_in_month: int = 30,
                                    get_operations: int = 0, write_operations: int = 0) -> Dict[str, Any]:
    """Calculate provider-backed storage cost with documented free tiers."""
    gb_months = max(0.0, bytes_stored / (1024 ** 3)) * max(0.0, lifetime_days) / max(1, days_in_month)
    billable_storage = max(0.0, gb_months - tariff.free_storage_gb_month)
    storage_cost = billable_storage * tariff.storage_per_gb_month
    billable_gets = max(0, int(get_operations) - tariff.free_get_per_month)
    get_cost = None if tariff.get_per_10k is None else billable_gets / 10_000 * tariff.get_per_10k
    billable_writes = max(0, int(write_operations) - tariff.free_write_per_month)
    write_cost = None if tariff.write_per_10k is None else billable_writes / 10_000 * tariff.write_per_10k
    components = {"storage": round(storage_cost, 6), "get_operations": None if get_cost is None else round(get_cost, 6),
                  "write_operations": None if write_cost is None else round(write_cost, 6)}
    known = [v for v in components.values() if v is not None]
    unknown = [k for k, v in components.items() if v is None]
    return {"provider": tariff.provider, "service": tariff.service, "region": tariff.region,
            "currency": tariff.currency, "pricing_version": tariff.pricing_version,
            "pricing_source": tariff.source, "pricing_source_updated": tariff.source_updated,
            "components": components, "known_cost": round(sum(known), 6),
            "unknown_components": unknown,
            "cost_status": "unknown" if not known else ("partial" if unknown else "known")}

def calculate_knowledge_cost(*, text_bytes: int = 0, embedding_tokens: int = 0,
                             vector_bytes: int = 0, metadata_bytes: int = 0, backup_bytes: int = 0,
                             lifetime_days: float = 0, retrieval_count: int = 0,
                             config: KnowledgeCostConfig) -> Dict[str, Any]:
    components = {
        "text": _cost(config.text_per_kb, text_bytes / 1024),
        "embedding": _cost(config.embedding_per_1k_tokens, embedding_tokens / 1000),
        "vector_storage": _cost(config.vector_storage_per_mb_day, vector_bytes / (1024 * 1024) * max(0.0, lifetime_days)),
        "metadata": _cost(config.metadata_per_kb_day, metadata_bytes / 1024 * max(0.0, lifetime_days)),
        "backup": _cost(config.backup_per_mb_day, backup_bytes / (1024 * 1024) * max(0.0, lifetime_days)),
        "retrieval": _cost(config.retrieval_per_1k_items, retrieval_count / 1000),
    }
    known = [value for value in components.values() if value is not None]
    unknown_components = [name for name, value in components.items() if value is None]
    return {"currency": CURRENCY, "pricing_version": PRICING_VERSION, "components": components,
            "known_cost": round(sum(known), 6), "unknown_components": unknown_components,
            "cost_status": "unknown" if not known else ("partial" if unknown_components else "known")}

def record_knowledge_operation(trace: Any, *, knowledge_item_id: str, operation: str,
                               invocation_id: Optional[str] = None, trace_id: Optional[str] = None,
                               session_id: Optional[str] = None, conversation_id: Optional[str] = None,
                               cost: Optional[Dict[str, Any]] = None, value: Optional[float] = None,
                               quality: Optional[float] = None) -> Dict[str, Any]:
    entry = {"knowledge_item_id": str(knowledge_item_id), "operation": operation,
             "invocation_id": str(invocation_id) if invocation_id is not None else None,
             "trace_id": str(trace_id or getattr(trace, "trace_id", "")),
             "session_id": str(session_id) if session_id is not None else None,
             "conversation_id": str(conversation_id) if conversation_id is not None else None,
             "cost": cost, "value": value, "quality": quality}
    trace.trace.setdefault("knowledge_operations", []).append(entry)
    trace.add_event("knowledge_operation", {"knowledge_item_id": entry["knowledge_item_id"], "operation": operation,
                                             "invocation_id": entry["invocation_id"], "trace_id": entry["trace_id"],
                                             "cost_status": (cost or {}).get("cost_status") if isinstance(cost, dict) else None})
    return entry

@dataclass(frozen=True)
class Policy:
    policy_id: str
    currency: str = CURRENCY
    reward_value_threshold: float = 1.0
    penalty_cost_ratio: float = 2.0
    max_penalty: float = 0.0

class PolicyBudgetController:
    def __init__(self, policy: Policy):
        self._policy = policy
    @property
    def policy_id(self) -> str:
        return self._policy.policy_id
    def evaluate(self, *, agent_id: str, invocation_id: str, trace_id: str, cost: float,
                 value: float, quality: Optional[float] = None, reason: Optional[str] = None) -> Dict[str, Any]:
        cost = max(0.0, float(cost)); value = float(value)
        if value >= self._policy.reward_value_threshold and cost == 0:
            outcome, amount, default_reason = "reward", value, "value threshold met"
        elif cost > 0 and value < cost * self._policy.penalty_cost_ratio:
            outcome, amount, default_reason = "penalty", min(cost, self._policy.max_penalty), "cost/value policy threshold"
        else:
            outcome, amount, default_reason = "neutral", 0.0, "within policy thresholds"
        return {"policy_id": self._policy.policy_id, "outcome": outcome, "reason": reason or default_reason,
                "amount": round(amount, 6), "currency": self._policy.currency, "agent_id": str(agent_id),
                "invocation_id": str(invocation_id), "trace_id": str(trace_id), "quality": quality}
