"""Supplementary behavioral regressions for orchestration_contract.

Covers the two behaviors not exercised by the accepted contract test suite:
- record_terminal raises KeyError for an unknown task_id
- active_task_id_for returns None when the work item's record is terminal
"""

import pytest

from agent_context import EvidenceRef
from agent_office.orchestration_contract import (
    OrchestratedTaskPacket,
    TaskRegistry,
)

_CONTRACT_HEAD = "abc123"
_CONTRACT_REF = EvidenceRef(kind="contract", ref=_CONTRACT_HEAD, version="accepted")


def _make_packet(
    task_id: str = "task-001",
    work_item_ref: str = "issue/1",
    stage: str = "contract",
    backend: str = "claude-direct",
    base_ref: str = "main",
    current_head: str = "head-abc",
    evidence_refs: tuple = (),
    accepted_contract_head: str | None = None,
) -> OrchestratedTaskPacket:
    return OrchestratedTaskPacket(
        task_id=task_id,
        work_item_ref=work_item_ref,
        stage=stage,
        backend=backend,
        base_ref=base_ref,
        current_head=current_head,
        evidence_refs=evidence_refs,
        accepted_contract_head=accepted_contract_head,
    )


class TestRecordTerminalUnknownTaskId:
    def test_raises_key_error_for_unknown_task_id(self) -> None:
        registry = TaskRegistry()
        with pytest.raises(KeyError):
            registry.record_terminal("nonexistent-task-id", "done")

    def test_raises_key_error_when_registry_is_empty(self) -> None:
        registry = TaskRegistry()
        with pytest.raises(KeyError):
            registry.record_terminal("any-id", "failed")

    def test_succeeds_for_registered_task(self) -> None:
        registry = TaskRegistry()
        packet = _make_packet()
        registry.register(packet)
        registry.record_terminal(packet.task_id, "done")  # must not raise


class TestActiveTaskIdForTerminal:
    def test_returns_none_for_missing_work_item(self) -> None:
        registry = TaskRegistry()
        assert registry.active_task_id_for("issue/999") is None

    def test_returns_none_after_terminal_state(self) -> None:
        registry = TaskRegistry()
        packet = _make_packet()
        registry.register(packet)
        registry.record_terminal(packet.task_id, "done")
        assert registry.active_task_id_for(packet.work_item_ref) is None

    def test_returns_none_after_failed_state(self) -> None:
        registry = TaskRegistry()
        packet = _make_packet()
        registry.register(packet)
        registry.record_terminal(packet.task_id, "failed")
        assert registry.active_task_id_for(packet.work_item_ref) is None

    def test_returns_task_id_while_active(self) -> None:
        registry = TaskRegistry()
        packet = _make_packet()
        registry.register(packet)
        assert registry.active_task_id_for(packet.work_item_ref) == packet.task_id
