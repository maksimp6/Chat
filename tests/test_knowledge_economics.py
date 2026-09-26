import json
import unittest

from knowledge_economics import (
    KnowledgeCostConfig,
    Policy,
    PolicyBudgetController,
    YANDEX_OBJECT_STORAGE_STANDARD_RU,
    calculate_knowledge_cost,
    calculate_provider_storage_cost,
    record_knowledge_operation,
)
from trace_manager import ExecutionTrace


class KnowledgeEconomicsTests(unittest.TestCase):
    def setUp(self):
        self.config = KnowledgeCostConfig(
            text_per_kb=1.0,
            embedding_per_1k_tokens=2.0,
            vector_storage_per_mb_day=0.5,
            metadata_per_kb_day=0.25,
            backup_per_mb_day=0.1,
            retrieval_per_1k_items=3.0,
        )

    def test_lifecycle_cost_keeps_components_separate(self):
        result = calculate_knowledge_cost(
            text_bytes=2048,
            embedding_tokens=1000,
            vector_bytes=1024 * 1024,
            metadata_bytes=1024,
            backup_bytes=2 * 1024 * 1024,
            lifetime_days=10,
            retrieval_count=500,
            config=self.config,
        )
        self.assertEqual(result["components"]["text"], 2.0)
        self.assertEqual(result["components"]["embedding"], 2.0)
        self.assertEqual(result["components"]["vector_storage"], 5.0)
        self.assertEqual(result["components"]["metadata"], 2.5)
        self.assertEqual(result["components"]["backup"], 2.0)
        self.assertEqual(result["components"]["retrieval"], 1.5)
        self.assertEqual(result["cost_status"], "known")
        self.assertEqual(result["known_cost"], 15.0)

    def test_yandex_object_storage_tariff_uses_free_tier(self):
        result = calculate_provider_storage_cost(
            bytes_stored=2 * 1024**3,
            lifetime_days=30,
            tariff=YANDEX_OBJECT_STORAGE_STANDARD_RU,
            days_in_month=30,
            get_operations=100_000,
        )
        self.assertEqual(result["components"]["storage"], 2.376)
        self.assertEqual(result["components"]["get_operations"], 0.0)
        self.assertIsNone(result["components"]["write_operations"])
        self.assertEqual(result["cost_status"], "partial")
        self.assertEqual(result["pricing_source_updated"], "2026-06-30")

    def test_unknown_rate_is_explicit(self):
        config = KnowledgeCostConfig(text_per_kb=1.0)
        result = calculate_knowledge_cost(text_bytes=1024, lifetime_days=1, config=config)
        self.assertEqual(result["cost_status"], "partial")
        self.assertIsNone(result["components"]["embedding"])
        self.assertIn("vector_storage", result["unknown_components"])
        self.assertEqual(result["known_cost"], 1.0)

    def test_knowledge_operation_is_linked_to_trace(self):
        trace = ExecutionTrace("trace-1")
        trace.set_context(invocation_id="inv-1", session_id="sess-1", conversation_id="conv-1")
        cost = calculate_knowledge_cost(text_bytes=1024, config=self.config)
        entry = record_knowledge_operation(
            trace,
            knowledge_item_id="knowledge-1",
            operation="create",
            invocation_id="inv-1",
            cost=cost,
            value=4.0,
            quality=0.9,
        )
        self.assertEqual(entry["trace_id"], "trace-1")
        self.assertEqual(trace.trace["knowledge_operations"][0]["knowledge_item_id"], "knowledge-1")
        self.assertEqual(trace.trace["events"][-1]["type"], "knowledge_operation")
        json.dumps(trace.finalize())

    def test_policy_is_deterministic_and_agent_cannot_change_limits(self):
        policy = Policy(policy_id="policy-1", penalty_cost_ratio=2.0, max_penalty=5.0)
        controller = PolicyBudgetController(policy)
        result = controller.evaluate(
            agent_id="agent-1",
            invocation_id="inv-1",
            trace_id="trace-1",
            cost=10,
            value=5,
            quality=0.4,
        )
        self.assertEqual(result["outcome"], "penalty")
        self.assertEqual(result["amount"], 5.0)
        self.assertEqual(result["policy_id"], "policy-1")
        with self.assertRaises(AttributeError):
            controller.policy_id = "agent-controlled"

    def test_policy_result_contains_required_correlation(self):
        controller = PolicyBudgetController(Policy(policy_id="policy-2"))
        result = controller.evaluate(
            agent_id="agent-2", invocation_id="inv-2", trace_id="trace-2", cost=0, value=0
        )
        self.assertEqual(result["outcome"], "neutral")
        for key in (
            "policy_id",
            "reason",
            "amount",
            "currency",
            "agent_id",
            "invocation_id",
            "trace_id",
        ):
            self.assertIn(key, result)


if __name__ == "__main__":
    unittest.main()
