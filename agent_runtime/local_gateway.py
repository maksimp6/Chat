"""Persistent gateway for remote Local Tool Agents without application login."""

from __future__ import annotations

import json
import math
import re
import time
from typing import Any, Mapping, Optional
from uuid import uuid4

from flask import Blueprint, jsonify, request

from core.clock import unix_time
from storage.db import get_store

local_agent_bp = Blueprint("local_agents", __name__, url_prefix="/api/local-agents")

_AGENT_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_MAX_CAPABILITIES = 64
_DEFAULT_LEASE_SECONDS = 30
_PROTOCOL_VERSION = 2


def _protocol_error():
    if request.headers.get("X-Logos-Agent-Protocol") != str(_PROTOCOL_VERSION):
        return jsonify(
            {
                "error": "agent_protocol_upgrade_required",
                "protocol_version": _PROTOCOL_VERSION,
            }
        ), 409
    return None


def _uncertain_result(row, reason: str) -> dict[str, Any]:
    return {
        "success": False,
        "data": None,
        "error": (
            "Исход выполнения локального инструмента неизвестен (in_doubt). "
            "Действие могло уже выполниться. Автоматически повторять его нельзя; "
            "сначала проверьте фактический результат."
        ),
        "metadata": {
            "job_id": row["id"],
            "agent_id": row["agent_id"],
            "claim_attempt": row.get("attempt", 0),
            "phase": "execution",
            "outcome": "in_doubt",
            "uncertain": True,
            "retry_safe": False,
            "reason": reason,
        },
    }


def _expire_job(tx, key: str, row, now: float):
    if row["status"] not in {"claimed", "running"}:
        return row
    if (
        row.get("claim_token")
        and row.get("lease_until") is not None
        and row["lease_until"] > now
    ):
        return row
    if row["status"] == "running":
        result = _uncertain_result(row, "execution_lease_expired")
        row = {
            **row,
            "status": "in_doubt",
            "lease_until": None,
            "result_json": _json(result),
            "error_json": _json(result["error"]),
        }
    else:
        # No /start acknowledgement was issued, so this claim cannot have executed.
        row = {
            **row,
            "status": "queued",
            "claimed_at": None,
            "lease_until": None,
            "claim_token": None,
        }
    tx.set(key, row)
    return row


def _timeout_job(tx, key: str, row):
    if row["status"] == "running":
        result = _uncertain_result(row, "wait_timeout")
        row = {
            **row,
            "status": "in_doubt",
            "lease_until": None,
            "result_json": _json(result),
            "error_json": _json(result["error"]),
        }
        tx.set(key, row)
    elif row["status"] in {"queued", "claimed"}:
        result = {
            "success": False,
            "data": None,
            "error": "Ожидание локального инструмента истекло до запуска; задание отменено.",
            "metadata": {
                "job_id": row["id"],
                "phase": "waiting",
                "outcome": "cancelled",
                "uncertain": False,
                "execution_started": False,
                "retry_safe": True,
            },
        }
        row = {
            **row,
            "status": "cancelled",
            "claim_token": None,
            "lease_until": None,
            "completed_at": unix_time(),
            "result_json": _json(result),
            "error_json": _json(result["error"]),
        }
        tx.set(key, row)
    return row


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
                "created_at": unix_time(),
                "claimed_at": None,
                "claim_token": None,
                "attempt": 0,
                "lease_until": None,
                "completed_at": None,
                "result_json": None,
                "error_json": None,
            },
        )
    return job_id


def wait_for_local_tool_job(job_id: str, *, timeout_seconds: float = 30.0) -> Any:
    """Wait within a bounded deadline for a persisted local-agent result."""
    timeout = float(timeout_seconds)
    if not math.isfinite(timeout) or timeout < 0 or timeout > 3600:
        raise ValueError("timeout_seconds must be between 0 and 3600")
    deadline = time.monotonic() + timeout
    while True:
        key = "local-job/" + str(job_id)
        with get_store().transaction() as tx:
            row = tx.get(key)
            if row is None:
                raise LookupError("local_tool_job_not_found")
            row = _expire_job(tx, key, row, unix_time())
            if time.monotonic() >= deadline:
                row = _timeout_job(tx, key, row)
        if row["status"] in {"completed", "failed", "cancelled", "in_doubt"}:
            result = _parse_json(row.get("result_json"), None)
            if row["status"] == "completed":
                return result if result is not None else {"success": True, "data": None}
            if isinstance(result, Mapping) and result.get("success") is False:
                return result
            error = _parse_json(row.get("error_json"), None)
            if error is None and isinstance(result, Mapping):
                error = result.get("error")
            return {
                "success": False,
                "data": None,
                "error": error or "local_agent_job_failed",
            }
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            continue
        time.sleep(min(0.05, remaining))


def _recover_expired_jobs(conn, agent_id: str) -> None:
    now = unix_time()
    for key in get_store().items(prefix="local-job/"):
        row = conn.get(key)
        if row is not None and row["agent_id"] == agent_id:
            _expire_job(conn, key, row, now)


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

    now = unix_time()

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
            "protocol_version": _PROTOCOL_VERSION,
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
    protocol_error = _protocol_error()
    if protocol_error is not None:
        return protocol_error
    data = request.get_json(silent=True)
    if (
        not isinstance(data, dict)
        or not data.get("job_id")
        or not data.get("claim_token")
    ):
        return jsonify({"error": "job_id_and_claim_token_required"}), 400
    now = unix_time()
    key = "local-agent/" + agent_id
    try:
        with get_store().transaction() as tx:
            agent = _require_agent(agent_id)
            job_key = "local-job/" + str(data["job_id"])
            row = tx.get(job_key)
            if row is None or row["agent_id"] != agent_id:
                return jsonify({"error": "job_not_found"}), 404
            row = _expire_job(tx, job_key, row, now)
            if row.get("claim_token") != data["claim_token"] or row["status"] not in {
                "claimed",
                "running",
            }:
                return jsonify({"error": "claim_not_active"}), 409
            tx.set(job_key, {**row, "lease_until": now + _DEFAULT_LEASE_SECONDS})
            tx.set(
                key, {**agent, "status": "online", "last_seen": now, "updated_at": now}
            )
    except LookupError:
        return jsonify({"error": "agent_not_found"}), 404

    return jsonify(
        {
            "status": "ok",
            "protocol_version": _PROTOCOL_VERSION,
            "agent_id": agent_id,
            "job_id": data["job_id"],
            "claim_token": data["claim_token"],
            "last_seen": now,
        }
    )


@local_agent_bp.get("/<agent_id>/poll")
def local_agent_poll(agent_id: str):
    protocol_error = _protocol_error()
    if protocol_error is not None:
        return protocol_error
    store = get_store()
    try:
        with store.transaction() as tx:
            agent = _require_agent(agent_id)
            _recover_expired_jobs(tx, agent_id)
            now = unix_time()
            queued = []
            for key in store.items(prefix="local-job/"):
                item = tx.get(key)
                if (
                    item is not None
                    and item["agent_id"] == agent_id
                    and item["status"] == "queued"
                ):
                    queued.append(item)
            row = min(queued, key=lambda item: item["created_at"]) if queued else None
            agent_key = "local-agent/" + agent_id
            tx.set(
                agent_key,
                {**agent, "status": "online", "last_seen": now, "updated_at": now},
            )
            if row is None:
                return jsonify(
                    {
                        "job": None,
                        "protocol_version": _PROTOCOL_VERSION,
                        "poll_after_seconds": 2,
                    }
                )
            lease_until = now + _DEFAULT_LEASE_SECONDS
            claim_token = uuid4().hex
            attempt = int(row.get("attempt") or 0) + 1
            tx.set(
                "local-job/" + row["id"],
                {
                    **row,
                    "status": "claimed",
                    "claimed_at": now,
                    "lease_until": lease_until,
                    "claim_token": claim_token,
                    "attempt": attempt,
                },
            )
    except LookupError:
        return jsonify({"error": "agent_not_found"}), 404
    return jsonify(
        {
            "protocol_version": _PROTOCOL_VERSION,
            "job": {
                "id": row["id"],
                "tool_name": row["tool_name"],
                "arguments": _parse_json(row["arguments_json"], {}),
                "metadata": _parse_json(row["metadata_json"], {}),
                "trace_id": row["trace_id"],
                "invocation_id": row["invocation_id"],
                "lease_until": lease_until,
                "claim_token": claim_token,
                "attempt": attempt,
            },
            "poll_after_seconds": 1,
        }
    )


@local_agent_bp.post("/<agent_id>/jobs/<job_id>/start")
def local_agent_start(agent_id: str, job_id: str):
    protocol_error = _protocol_error()
    if protocol_error is not None:
        return protocol_error
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not data.get("claim_token"):
        return jsonify({"error": "claim_token_required"}), 400
    key = "local-job/" + job_id
    try:
        with get_store().transaction() as tx:
            _require_agent(agent_id)
            row = tx.get(key)
            if row is None or row["agent_id"] != agent_id:
                return jsonify({"error": "job_not_found"}), 404
            row = _expire_job(tx, key, row, unix_time())
            if row.get("claim_token") != data["claim_token"] or row["status"] not in {
                "claimed",
                "running",
            }:
                return jsonify({"error": "claim_not_active"}), 409
            if row["status"] == "claimed":
                row = {**row, "status": "running", "started_at": unix_time()}
                tx.set(key, row)
    except LookupError:
        return jsonify({"error": "agent_not_found"}), 404
    return jsonify(
        {
            "status": "running",
            "protocol_version": _PROTOCOL_VERSION,
            "job_id": job_id,
            "claim_token": row["claim_token"],
            "attempt": row["attempt"],
        }
    )


@local_agent_bp.post("/<agent_id>/jobs/<job_id>/result")
def local_agent_result(agent_id: str, job_id: str):

    protocol_error = _protocol_error()
    if protocol_error is not None:
        return protocol_error
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not data.get("claim_token"):
        return jsonify({"error": "claim_token_required"}), 400
    status = str(data.get("status") or "").strip().lower()
    if status not in {"completed", "failed"}:
        return jsonify({"error": "status_must_be_completed_or_failed"}), 400
    result = data.get("result")
    if not isinstance(result, dict) or result.get("success") is not (
        status == "completed"
    ):
        return jsonify({"error": "result_success_must_match_status"}), 400

    key = "local-job/" + job_id
    try:
        with get_store().transaction() as tx:
            # Raises LookupError for an unknown agent, which becomes a 404 below.
            _require_agent(agent_id)
            row = tx.get(key)
            if row is None or row["agent_id"] != agent_id:
                return jsonify({"error": "job_not_found"}), 404
            if row.get("claim_token") != data["claim_token"]:
                return jsonify({"error": "claim_mismatch"}), 409
            if row["status"] in {"completed", "failed"}:
                if (
                    row["status"] != status
                    or _parse_json(row.get("result_json"), None) != result
                ):
                    return jsonify({"error": "result_conflict"}), 409
                return jsonify(
                    {
                        "status": "ok",
                        "protocol_version": _PROTOCOL_VERSION,
                        "job_id": job_id,
                        "claim_token": row["claim_token"],
                        "idempotent": True,
                    }
                )
            # A late result may resolve uncertainty for the SAME started claim.
            # An expired running job is never offered to another worker.
            if row["status"] not in {"running", "in_doubt"}:
                return jsonify({"error": "job_not_running"}), 409
            tx.set(
                key,
                {
                    **row,
                    "status": status,
                    "result_json": _json(result),
                    "error_json": _json(result.get("error")),
                    "completed_at": unix_time(),
                    "lease_until": None,
                },
            )
    except LookupError:
        return jsonify({"error": "agent_not_found"}), 404

    return jsonify(
        {
            "status": "ok",
            "protocol_version": _PROTOCOL_VERSION,
            "job_id": job_id,
            "claim_token": data["claim_token"],
        }
    )


@local_agent_bp.get("/health")
def local_agent_health():
    cutoff = unix_time() - 90
    rows = sorted(
        get_store().items(prefix="local-agent/").values(), key=lambda row: row["id"]
    )

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
    return jsonify({"protocol_version": _PROTOCOL_VERSION, "agents": agents})


__all__ = [
    "enqueue_local_tool_job",
    "wait_for_local_tool_job",
    "init_local_agent_tables",
    "local_agent_bp",
]
