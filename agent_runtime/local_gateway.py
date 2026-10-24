"""Persistent gateway for remote Local Tool Agents without application login."""

from __future__ import annotations

import json
import math
import re
import time
from typing import Any, Mapping, Optional
from uuid import uuid4

from flask import Blueprint, jsonify, request

from storage.db import get_store

local_agent_bp = Blueprint("local_agents", __name__, url_prefix="/api/local-agents")

_AGENT_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_MAX_CAPABILITIES = 64
_DEFAULT_LEASE_SECONDS = 30


def _now() -> float:
    return time.time()


def _json(value: Any) -> str:
    return json.dumps(
        value if value is not None else {},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _parse_json(value: Optional[str], default: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def _agent_row(agent_id: str) -> dict[str, Any] | None:
    record = get_store().get("local-agent/" + agent_id)
    return dict(record) if record is not None else None


def _require_agent(agent_id: str) -> dict[str, Any]:
    if not _AGENT_ID_RE.fullmatch(agent_id):
        raise LookupError("agent_not_found")
    row = _agent_row(agent_id)
    if row is None:
        raise LookupError("agent_not_found")
    return row


def init_local_agent_tables() -> None:
    get_store()


def enqueue_local_tool_job(
    agent_id: str,
    tool_name: str,
    arguments: Mapping[str, Any],
    *,
    metadata: Optional[Mapping[str, Any]] = None,
    trace_id: Optional[str] = None,
    invocation_id: Optional[str] = None,
) -> str:
    """Persist a tool job for a connected agent.

    This is intentionally an internal Python API. Public callers should reach
    it through Universal Tool Executor/policy rather than a public enqueue route.
    """
    if not _AGENT_ID_RE.fullmatch(agent_id):
        raise ValueError("invalid agent id")
    if not tool_name or not isinstance(arguments, Mapping):
        raise ValueError("tool_name and object arguments are required")

    job_id = str(uuid4())
    with get_store().transaction() as tx:
        if tx.get("local-agent/" + agent_id) is None:
            raise LookupError("agent_not_found")
        tx.set(
            "local-job/" + job_id,
            {
                "id": job_id,
                "agent_id": agent_id,
                "tool_name": tool_name,
                "arguments_json": _json(dict(arguments)),
                "metadata_json": _json(dict(metadata or {})),
                "trace_id": trace_id,
                "invocation_id": invocation_id,
                "status": "queued",
                "created_at": _now(),
                "claimed_at": None,
                "lease_until": None,
                "completed_at": None,
                "result_json": None,
                "error_json": None,
            },
        )
    return job_id


def wait_for_local_tool_job(
    job_id: str, *, timeout_seconds: float = 30.0
) -> Any:
    """Wait within a bounded deadline for a persisted local-agent result."""
    timeout = float(timeout_seconds)
    if not math.isfinite(timeout) or timeout < 0 or timeout > 3600:
        raise ValueError("timeout_seconds must be between 0 and 3600")
    deadline = time.monotonic() + timeout
    while True:
        row = get_store().get("local-job/" + str(job_id))
        if row is None:
            raise LookupError("local_tool_job_not_found")
        if row["status"] in {"completed", "failed"}:
            result = _parse_json(row.get("result_json"), None)
            if row["status"] == "completed":
                return result if result is not None else {"success": True, "data": None}
            if isinstance(result, Mapping) and result.get("success") is False:
                return result
            error = _parse_json(row.get("error_json"), None)
            if error is None and isinstance(result, Mapping):
                error = result.get("error")
            return {"success": False, "data": None, "error": error or "local_agent_job_failed"}
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        time.sleep(min(0.05, remaining))


def _recover_expired_jobs(conn, agent_id: str) -> None:
    now = _now()
    for key, row in get_store().items(prefix="local-job/").items():
        if (
            row["agent_id"] == agent_id
            and row["status"] == "running"
            and row["lease_until"] is not None
            and row["lease_until"] < now
        ):
            conn.set(key, {**row, "status": "queued", "claimed_at": None, "lease_until": None})


def _renew_running_leases(conn, agent_id: str, now: float) -> None:
    # A job that is still executing must keep its lease, or the next poll requeues it and a
    # second agent run repeats any side effect the first one already produced.
    for key, row in get_store().items(prefix="local-job/").items():
        if row["agent_id"] == agent_id and row["status"] == "running":
            conn.set(key, {**row, "lease_until": now + _DEFAULT_LEASE_SECONDS})


@local_agent_bp.post("/register")
def register_local_agent():
    data = request.get_json(silent=True) or {}
    agent_id = str(data.get("agent_id") or f"android-{uuid4().hex[:12]}").strip()
    name = str(data.get("name") or "Logos Android Agent").strip()
    version = str(data.get("version") or "1.0").strip()
    capabilities = data.get("capabilities") or []

    if not _AGENT_ID_RE.fullmatch(agent_id):
        return jsonify({"error": "invalid_agent_id"}), 400
    if not name:
        return jsonify({"error": "name_required"}), 400
    if not isinstance(capabilities, list) or len(capabilities) > _MAX_CAPABILITIES:
        return jsonify({"error": "invalid_capabilities"}), 400
    capabilities = [str(item).strip() for item in capabilities if str(item).strip()]

    now = _now()

    key = "local-agent/" + agent_id
    with get_store().transaction() as tx:
        old = tx.get(key)
        tx.set(
            key,
            {
                "id": agent_id,
                "name": name,
                "version": version,
                "capabilities_json": _json(capabilities),
                "status": "online",
                "last_seen": now,
                "created_at": old["created_at"] if old else now,
                "updated_at": now,
            },
        )

    return jsonify(
        {
            "agent": {
                "id": agent_id,
                "name": name,
                "version": version,
                "capabilities": capabilities,
                "status": "online",
            },
        }
    ), 201


@local_agent_bp.post("/<agent_id>/heartbeat")
def local_agent_heartbeat(agent_id: str):

    now = _now()
    key = "local-agent/" + agent_id
    try:
        with get_store().transaction() as tx:
            agent = _require_agent(agent_id)
            row = agent
            tx.set(key, {**row, "status": "online", "last_seen": now, "updated_at": now})
            _renew_running_leases(tx, agent_id, now)
    except LookupError:
        return jsonify({"error": "agent_not_found"}), 404

    return jsonify({"status": "ok", "agent_id": agent_id, "last_seen": now})


@local_agent_bp.get("/<agent_id>/poll")
def local_agent_poll(agent_id: str):

    store = get_store()
    try:
        with store.transaction() as tx:
            agent = _require_agent(agent_id)
            _recover_expired_jobs(tx, agent_id)
            now = _now()
            queued = []
            for key in store.items(prefix="local-job/"):
                item = tx.get(key)
                if item is not None and item["agent_id"] == agent_id and item["status"] == "queued":
                    queued.append(item)
            row = min(queued, key=lambda item: item["created_at"]) if queued else None
            agent_key = "local-agent/" + agent_id
            tx.set(agent_key, {**agent, "status": "online", "last_seen": now, "updated_at": now})
            if row is None:
                return jsonify({"job": None, "poll_after_seconds": 2})
            lease_until = now + _DEFAULT_LEASE_SECONDS
            tx.set(
                "local-job/" + row["id"],
                {**row, "status": "running", "claimed_at": now, "lease_until": lease_until},
            )
    except LookupError:
        return jsonify({"error": "agent_not_found"}), 404
    return jsonify(
        {
            "job": {
                "id": row["id"],
                "tool_name": row["tool_name"],
                "arguments": _parse_json(row["arguments_json"], {}),
                "metadata": _parse_json(row["metadata_json"], {}),
                "trace_id": row["trace_id"],
                "invocation_id": row["invocation_id"],
                "lease_until": lease_until,
            },
            "poll_after_seconds": 1,
        }
    )


@local_agent_bp.post("/<agent_id>/jobs/<job_id>/result")
def local_agent_result(agent_id: str, job_id: str):

    data = request.get_json(silent=True) or {}
    status = str(data.get("status") or "").strip().lower()
    if status not in {"completed", "failed"}:
        return jsonify({"error": "status_must_be_completed_or_failed"}), 400

    key = "local-job/" + job_id
    try:
        with get_store().transaction() as tx:
            agent = _require_agent(agent_id)
            row = tx.get(key)
            if row is None or row["agent_id"] != agent_id:
                return jsonify({"error": "job_not_found"}), 404
            if row["status"] in {"completed", "failed"}:
                return jsonify({"status": "ok", "idempotent": True})
            # Only the job this agent claimed through poll may be completed. A queued job was never
            # run, so a result for it would be invented.
            if row["status"] != "running":
                return jsonify({"error": "job_not_running"}), 409
            tx.set(
                key,
                {
                    **row,
                    "status": status,
                    "result_json": _json(data.get("result")),
                    "error_json": _json(data.get("error")),
                    "completed_at": _now(),
                    "lease_until": None,
                },
            )
    except LookupError:
        return jsonify({"error": "agent_not_found"}), 404

    return jsonify({"status": "ok", "job_id": job_id})


@local_agent_bp.get("/health")
def local_agent_health():
    cutoff = _now() - 90
    rows = sorted(get_store().items(prefix="local-agent/").values(), key=lambda row: row["id"])

    agents = []
    for row in rows:
        alive = bool(row["last_seen"] and row["last_seen"] >= cutoff)
        agents.append(
            {
                "id": row["id"],
                "name": row["name"],
                "version": row["version"],
                "capabilities": _parse_json(row["capabilities_json"], []),
                "status": "online" if alive else "offline",
                "last_seen": row["last_seen"],
                "updated_at": row["updated_at"],
            }
        )
    return jsonify({"agents": agents})


__all__ = ["enqueue_local_tool_job", "wait_for_local_tool_job", "init_local_agent_tables", "local_agent_bp"]
