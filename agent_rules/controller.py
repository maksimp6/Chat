"""Candidate enforcement boundary; not installed in production by this PR.

Host adapters own observations/verification. Model arguments cannot supply them.
Do not expose controller construction or policy files as agent tools.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from types import MappingProxyType
from typing import Any, Protocol

RULES = MappingProxyType(
    {
        "AR-001": "BLOCKED",
        "AR-002": "FAILED",
        "AR-003": "FAILED",
        "AR-004": "FAILED",
        "AR-005": "REVIEW",
        "AR-006": "BLOCKED",
    }
)


def load_registry(path: Path) -> tuple[dict[str, str], ...]:
    """Reject omitted, duplicate, activated or weakened candidate rules."""
    document = json.loads(path.read_text(encoding="utf-8"))
    rules = document.get("rules", [])
    if document.get("version") != 1 or len(rules) != len(RULES):
        raise ValueError("Invalid candidate registry")
    seen = set()
    for rule in rules:
        identifier = rule.get("id")
        if (
            identifier in seen
            or identifier not in RULES
            or rule.get("violation") != RULES[identifier]
            or rule.get("state") != "proposed"
            or not rule.get("description")
        ):
            raise ValueError("Invalid candidate rule")
        seen.add(identifier)
    return tuple(dict(rule) for rule in rules)


class EventTrace(Protocol):
    def add_event(self, event_type: str, payload: dict[str, Any]) -> None: ...


@dataclass(frozen=True)
class TaskScope:
    task_id: str
    agent_id: str
    device_id: str
    destination: str

    def __post_init__(self) -> None:
        if not all(
            isinstance(value, str) and value.strip()
            for value in (self.task_id, self.agent_id, self.device_id, self.destination)
        ):
            raise ValueError("Task identity and target are required")


class GuardedTask:
    """One serialized task, including all retries/providers using its adapter.

    The host must keep a single instance alive across continuations, resolve tool
    destinations without executing them and independently verify task-specific
    outcomes. A tool's success flag alone is not evidence. Existing executor
    authorization and approval hooks remain mandatory and unchanged.
    """

    def __init__(
        self,
        scope: TaskScope,
        executor: Any,
        trace: EventTrace,
        *,
        observe_device: Callable[[], str],
        observe_destination: Callable[[Any], str],
        verify_result: Callable[[Mapping[str, Any]], bool],
    ) -> None:
        self.scope = scope
        self.executor = executor
        self.trace = trace
        self.observe_device = observe_device
        self.observe_destination = observe_destination
        self.verify_result = verify_result
        self._lock = RLock()
        self._salt = secrets.token_bytes(32)
        self._failed: set[str] = set()
        self._proposals: list[dict[str, Any]] = []
        self._proposed: set[str] = set()
        self._attempts = 0
        self._verified = 0
        self._closed = False
        self._violated = False
        load_registry(Path(__file__).with_name("registry.json"))

    @property
    def proposals(self) -> list[dict[str, Any]]:
        return deepcopy(self._proposals)

    def _violation(self, rule: str, phase: str, method: str = "") -> dict[str, Any]:
        status = RULES[rule]
        # No arguments, provider errors, credentials, paths or observation values.
        self.trace.add_event(
            "agent_rule_violation",
            {
                "rule_id": rule,
                "status": status,
                "phase": phase,
                "method_id": method,
                "policy_version": 1,
            },
        )
        if rule != "AR-005":
            self._violated = True
        return {"success": False, "status": status, "rule_id": rule}

    def execute(self, call: Any, **hooks: Any) -> dict[str, Any]:
        with self._lock:
            if self._closed:
                raise RuntimeError("Task is closed")
            admission = self._admit(call)
            if admission is not None:
                return admission
            try:
                encoded = json.dumps(
                    [call.tool_name, dict(call.arguments), call.transport],
                    sort_keys=True,
                    allow_nan=False,
                ).encode()
            except (TypeError, ValueError):
                return self._violation("AR-006", "before")
            method = hmac.new(self._salt, encoded, hashlib.sha256).hexdigest()
            if method in self._failed:
                self._propose(method)
                return self._violation("AR-006", "before", method)
            return self._run(call, method, hooks)

    def _propose(self, method: str) -> None:
        if method in self._proposed:
            return
        self._proposed.add(method)
        self._proposals.append(
            {
                "state": "proposed",
                "source_rule": "AR-005",
                "method_id": method,
                "regression": {
                    "scenario": "repeat failed method",
                    "expected": "BLOCKED",
                    "test": "tests/test_agent_rules.py::test_failed_method_blocked_even_new_call_id",
                },
            }
        )
        self._violation("AR-005", "learning", method)

    @staticmethod
    def _host_check(callback: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        try:
            return callback(*args, **kwargs)
        except Exception:  # noqa: BLE001 - untrusted host failure must stop admission; never leak errors
            return None

    def _admit(self, call: Any) -> dict[str, Any] | None:
        if self._host_check(self.observe_device) != self.scope.device_id:
            return self._violation("AR-001", "before")
        if self._host_check(self.observe_destination, call) != self.scope.destination:
            return self._violation("AR-004", "before")
        return None

    def _fail(self, method: str) -> dict[str, Any]:
        self._failed.add(method)
        result = self._violation("AR-003", "after", method)
        if len(self._failed) > 1:
            self._propose(method)
        return result

    def _run(self, call: Any, method: str, hooks: dict[str, Any]) -> dict[str, Any]:
        self._attempts += 1
        result = self._host_check(self.executor.execute, call, **hooks)
        if not isinstance(result, Mapping) or result.get("success") is not True:
            return self._fail(method)
        if self._host_check(self.verify_result, result) is not True:
            return self._fail(method)
        self._verified += 1
        self.trace.add_event(
            "agent_rule_check",
            {
                "status": "VERIFIED",
                "phase": "after",
                "method_id": method,
                "policy_version": 1,
            },
        )
        return {**result, "status": "VERIFIED"}

    def finish(self, *, claim_success: bool) -> dict[str, Any]:
        with self._lock:
            if self._closed:
                raise RuntimeError("Task is closed")
            self._closed = True
            if self._attempts == 0:
                return self._violation("AR-002", "closeout")
            if claim_success and (self._violated or self._verified != self._attempts):
                return self._violation("AR-003", "closeout")
            return {
                "success": claim_success,
                "status": "VERIFIED" if claim_success else "FAILED",
            }
