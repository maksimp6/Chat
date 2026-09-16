"""Semantic economics for knowledge items and agent operations.

This module records economic facts separately from AI inference billing.  Rates are
configuration inputs, not values inferred from latency or provider conversation IDs.
Policy decisions are deterministic and owned by PolicyBudgetController, never by
an agent.
"""
from dataclasses import dataclass
from typing import Any, Dict, Optional


CURRENCY = "RUB"
PRICING_VERSION = "knowledge-config-v1"


@dataclass(frozen=True)
class KnowledgeCostConfig:
    """Configurable unit rates for knowledge lifecycle accounting."""
    text_per_kb: Optional[float] = None
    embedding_per_1k_tokens: Optional[float] = None
    vector_storage_per_mb_day: Optional[float] = None
    metadata_per_kb_day: Optional[float] = None
    backup_per_mb_day: Optional[float] = None
    retrieval_per_1k_items: Optional[float] = None


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
) -> Dict[str, Any]:
    """Calculate lifecycle cost without conflating storage with inference.

    ``None`` means the configured price is unknown.  Unknown components are not
    silently converted to zero, which keeps Treasury calculations honest.
    """
    components = {
        "text": _cost(config.text_per_kb, text_bytes / 1024),
        "embedding": _cost(config.embedding_per_1k_tokens, embedding_tokens / 1000),
        "vector_storage": _cost(
            config.vector_storage_per_mb_day,
            vector_bytes / (1024 * 1024) * max(0.0, lifetime_days),
        ),
        "metadata": _cost(
            config.metadata_per_kb_day,
            metadata_bytes / 1024 * max(0.0, lifetime_days),
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
        "cost_status": "unknown" if len(known) == 0 else ("partial" if unknown_components else "known"),
    }


def record_knowledge_operation(
    trace: Any,
    *,
    knowledge_item_id: str,
    operation: str,
    invocation_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    session_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    cost: Optional[Dict[str, Any]] = None,
    value: Optional[float] = None,
    quality: Optional[float] = None,
) -> Dict[str, Any]:
    """Attach a knowledge operation to an ExecutionTrace instance."""
    trace_data = trace.trace
    entry = {
        "knowledge_item_id": str(knowledge_item_id),
        "operation": operation,
        "invocation_id": str(invocation_id) if invocation_id is not None else None,
        "trace_id": str(trace_id or getattr(trace, "trace_id", "")),
        "session_id": str(session_id) if session_id is not None else None,
        "conversation_id": str(conversation_id) if conversation_id is not None else None,
        "cost": cost,
        "value": value,
        "quality": quality,
    }
    trace_data.setdefault("knowledge_operations", []).append(entry)
    trace.add_event("knowledge_operation", {
        "knowledge_item_id": entry["knowledge_item_id"],
        "operation": operation,
        "invocation_id": entry["invocation_id"],
        "trace_id": entry["trace_id"],
        "cost_status": (cost or {}).get("cost_status") if isinstance(cost, dict) else None,
    })
    return entry


@dataclass(frozen=True)
class Policy:
    """Deterministic economic policy owned outside the agent."""
    policy_id: str
    currency: str = CURRENCY
    reward_value_threshold: float = 1.0
    penalty_cost_ratio: float = 2.0
    max_penalty: float = 0.0


class PolicyBudgetController:
    """Evaluate economic outcomes without granting agents budget authority."""

    def __init__(self, policy: Policy):
        self._policy = policy

    @property
    def policy_id(self) -> str:
        return self._policy.policy_id

    def evaluate(
        self,
        *,
        agent_id: str,
        invocation_id: str,
        trace_id: str,
        cost: float,
        value: float,
        quality: Optional[float] = None,
        reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        cost = max(0.0, float(cost))
        value = float(value)
        if value >= self._policy.reward_value_threshold and cost == 0:
            outcome, amount, default_reason = "reward", value, "value threshold met"
        elif cost > 0 and value < cost * self._policy.penalty_cost_ratio:
            outcome, amount, default_reason = "penalty", min(cost, self._policy.max_penalty), "cost/value policy threshold"
        else:
            outcome, amount, default_reason = "neutral", 0.0, "within policy thresholds"
        return {
            "policy_id": self._policy.policy_id,
            "outcome": outcome,
            "reason": reason or default_reason,
            "amount": round(amount, 6),
            "currency": self._policy.currency,
            "agent_id": str(agent_id),
            "invocation_id": str(invocation_id),
            "trace_id": str(trace_id),
            "quality": quality,
        }
