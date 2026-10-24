"""Regression tests for defects found in the read-only audit of the non-app modules."""

import json
import struct
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from agent_office import observer
from agent_office.dispatch_model import (
    SolutionReviewArtifact,
    SolutionReviewRequiredError,
    assert_solution_review_accepted_for_head,
)
from agent_shell.runner import run_next
from agent_shell.store import TaskStore
from economy.budget_controller import (
    AccountType,
    BudgetAccount,
    BudgetController,
    BudgetLimitExceeded,
    BudgetLimits,
    InvalidOperation,
    _money,
)
from memory_engine import MemoryStore, StoreError
from memory_engine.service import _operate
from printer_service.printers.bambu_a1mini import probe


def test_refund_of_earlier_spend_does_not_free_todays_daily_loss_headroom():
    limits = BudgetLimits("500", "100", "1000")
    account = BudgetAccount(
        AccountType.REAL,
        "EUR",
        allocated="500",
        spent="100",
        loss_today="0",
        loss_period="2026-09-24",
        limits=limits,
    )
    now = datetime(2026, 9, 25, 12, tzinfo=UTC)
    controller = BudgetController(
        "B",
        account,
        BudgetAccount(AccountType.DEMO, "EUR", allocated="500", limits=limits),
        clock=lambda: now,
    )
    controller.spend("50", account_type=AccountType.REAL)
    # The refund returns yesterday's money, so today's 50 is still counted as loss.
    controller.refund("100", account_type=AccountType.REAL)
    assert controller.account(AccountType.REAL).loss_today == Decimal("50.00")
    with pytest.raises(BudgetLimitExceeded):
        controller.spend("60", account_type=AccountType.REAL)


def test_same_period_refund_still_reverses_daily_loss():
    controller = BudgetController(
        "B",
        BudgetAccount(
            AccountType.REAL, "EUR", allocated="100", limits=BudgetLimits("50", "100", "300")
        ),
        BudgetAccount(
            AccountType.DEMO, "EUR", allocated="100", limits=BudgetLimits("50", "100", "300")
        ),
    )
    controller.spend("20", account_type=AccountType.REAL)
    controller.refund("5", account_type=AccountType.REAL)
    assert controller.account(AccountType.REAL).loss_today == Decimal("15.00")


def test_sub_cent_amounts_round_up_and_bad_numbers_raise_budget_error():
    assert _money("0.004") == Decimal("0.01")
    for bad in ("nan", "inf", "abc", "-1"):
        with pytest.raises(InvalidOperation):
            _money(bad)


def test_money_enters_only_through_the_policy_checked_replenishment():
    controller = BudgetController(
        "B",
        BudgetAccount(AccountType.REAL, "EUR", limits=BudgetLimits("50", "100", "300")),
        BudgetAccount(AccountType.DEMO, "EUR", limits=BudgetLimits("50", "100", "300")),
    )
    assert not hasattr(controller, "allocate")
    controller.request_replenishment(
        AccountType.REAL, "10", requested_by="owner", policy_check=lambda *_: True
    )
    assert controller.account(AccountType.REAL).allocated == Decimal("10.00")


def test_nan_is_rejected_at_staging_and_never_poisons_later_commits(tmp_path):
    with MemoryStore(tmp_path / "m.memory") as store:
        with pytest.raises(StoreError):
            store.set("bad", float("nan"))
        store.set("good", 1)
        assert store.commit() == 1


def test_service_answers_a_rejected_write_and_keeps_serving(tmp_path):
    with MemoryStore(tmp_path / "s.memory") as store:
        assert _operate(store, {"op": "set", "key": "bad", "value": float("nan")}) == {
            "ok": False,
            "error": "rejected",
        }
        assert _operate(store, {"op": "set", "key": "good", "value": "x"})["ok"] is True
        assert _operate(store, {"op": "get", "key": ""}) == {
            "ok": False,
            "error": "invalid operation",
        }


def test_nan_result_fails_the_task_instead_of_leaving_it_running(tmp_path):
    store = TaskStore(tmp_path / "shell.memory")
    task_id = store.add(role="infra-engineer", title="t", kind="echo")
    task = run_next(store, {"echo": lambda payload: {"value": float("nan")}})
    assert task["id"] == task_id
    assert (task["status"], task["error"]) == ("failed", "invalid_result")


def test_implementation_role_cannot_sign_a_solution_review():
    artifact = SolutionReviewArtifact("ACCEPTED", "abc123", "backend-engineer")
    with pytest.raises(SolutionReviewRequiredError):
        assert_solution_review_accepted_for_head(artifact, "abc123")
    accepted = SolutionReviewArtifact("ACCEPTED", "abc123", "team-lead")
    assert_solution_review_accepted_for_head(accepted, "abc123")


def test_backend_acknowledgement_needs_a_visible_body():
    assert not observer._is_backend_acknowledgement("codex", "", "commented")
    assert not observer._is_backend_acknowledgement("codex", "   ", "reviewed")
    assert observer._is_backend_acknowledgement("codex", "Working on it", "commented")


class _ClosingSocket:
    """Mimics a real socket: getpeername() fails once the with-block has closed it."""

    def __init__(self, reply: bytes) -> None:
        self._buffer = bytearray(reply)
        self._open = True

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self._open = False

    def sendall(self, _data: bytes) -> None:
        if not self._open:
            raise OSError(9, "Bad file descriptor")

    def recv(self, size: int) -> bytes:
        if not self._open:
            raise OSError(9, "Bad file descriptor")
        chunk = bytes(self._buffer[:size])
        del self._buffer[:size]
        return chunk

    def getpeername(self):
        if not self._open:
            raise OSError(9, "Bad file descriptor")
        return ("192.168.1.50", 3000)


def test_probe_reads_peer_address_while_the_socket_is_still_open(monkeypatch):
    body = json.dumps(
        {"login": {"command": "detect", "sequence_id": "1", "model": "N1", "version": "1.0"}},
        separators=(",", ":"),
    ).encode()
    reply = b"\xa5\xa5" + struct.pack("<H", len(body) + 6) + body + b"\xa7\xa7"
    sock = _ClosingSocket(reply)
    monkeypatch.setattr("printer_service.printers.lan_socket.connect_lan", lambda *_a, **_k: sock)
    identity = probe("192.168.1.50")
    assert identity.host == "192.168.1.50"
    assert identity.model_code == "N1"
