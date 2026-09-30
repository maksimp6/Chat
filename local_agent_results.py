"""Bounded result retrieval shared by local-agent gateway transports.

This module has no Flask or database-driver dependency. Connection factories
must provide execute/fetchone/close and mapping-style rows, as db.get_conn does.
It is an internal execution helper, not a public route or an authorization layer.
"""

from __future__ import annotations

import json
import math
import time
from typing import Any, Callable, Mapping

_MAX_WAIT_SECONDS = 300.0
_POLL_SECONDS = 0.05
_MAX_RESULT_CHARACTERS = 1_048_576
_MAX_JSON_DEPTH = 64


def _reject_json_constant(_value: str) -> None:
    raise ValueError("non_finite_json_number")


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("non_finite_json_number")
    return number


def _check_json_depth(payload: str) -> None:
    depth = 0
    quoted = False
    escaped = False
    for char in payload:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            if depth > _MAX_JSON_DEPTH:
                raise ValueError("json_depth_exceeded")
        elif char in "]}":
            depth -= 1


def _terminal_result(row: Mapping[str, Any]) -> dict[str, Any]:
    metadata = {
        "job_id": row["id"],
        "agent_id": row["agent_id"],
        "trace_id": row["trace_id"],
        "invocation_id": row["invocation_id"],
        "job_status": row["status"],
    }

    def failure(code: str) -> dict[str, Any]:
        # Remote exception text and metadata may contain secrets. Do not echo them.
        return {"success": False, "data": None, "error": code, "metadata": metadata}

    if row["status"] == "failed":
        return failure("local_agent_job_failed")
    if row["status"] != "completed":
        return failure("local_agent_job_invalid_status")

    payload = row["result_json"]
    if not isinstance(payload, str) or len(payload) > _MAX_RESULT_CHARACTERS:
        return failure("local_agent_result_invalid")
    try:
        _check_json_depth(payload)
        result = json.loads(
            payload, parse_constant=_reject_json_constant, parse_float=_finite_float
        )
    except (ValueError, TypeError, RecursionError):
        return failure("local_agent_result_invalid")

    if isinstance(result, Mapping) and "success" in result:
        if not isinstance(result["success"], bool):
            return failure("local_agent_result_invalid")
        if not result["success"]:
            return failure("local_agent_tool_failed")
        data = (
            result["data"]
            if "data" in result
            else {
                key: value
                for key, value in result.items()
                if key not in {"success", "error", "metadata"}
            }
        )
    elif isinstance(result, Mapping) and result.get("error"):
        return failure("local_agent_tool_failed")
    else:
        data = result
    return {"success": True, "data": data, "error": None, "metadata": metadata}


def wait_for_job_result(
    connection_factory: Callable[[], Any],
    job_id: str,
    *,
    timeout_seconds: float = 30.0,
) -> dict[str, Any] | None:
    """Read a terminal result, or return None when the polling deadline expires.

    Even a zero timeout performs one read. Waiting never claims, requeues,
    cancels or deletes a job. A timeout is not proof that execution did not occur.
    Database connections are closed before sleeping; driver-level connection and
    query timeouts remain the responsibility of the configured database backend.
    """
    if not isinstance(job_id, str) or not job_id.strip() or len(job_id) > 128:
        raise ValueError("invalid local-agent job id")
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not 0 <= timeout_seconds <= _MAX_WAIT_SECONDS
        or not math.isfinite(timeout_seconds)
    ):
        raise ValueError("local-agent timeout must be finite and between 0 and 300 seconds")

    deadline = time.monotonic() + timeout_seconds
    while True:
        conn = connection_factory()
        try:
            row = conn.execute(
                "SELECT id, agent_id, trace_id, invocation_id, status, result_json "
                "FROM local_agent_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            raise LookupError("local_agent_job_not_found")
        if row["status"] not in {"queued", "running"}:
            return _terminal_result(row)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        time.sleep(min(_POLL_SECONDS, remaining))
