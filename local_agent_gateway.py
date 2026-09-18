"""Secure, persistent gateway for remote Local Tool Agents."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import time
from typing import Any, Mapping, Optional
from uuid import uuid4

from flask import Blueprint, jsonify, request

from db import get_conn

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


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _bearer_token() -> Optional[str]:
    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith("Bearer "):
        return None
    token = authorization[7:].strip()
    return token or None


def _require_bootstrap() -> None:
    expected = os.getenv("ALICE_LOCAL_AGENT_BOOTSTRAP_TOKEN", "").strip()
    token = _bearer_token()
    if not expected or not token or not secrets.compare_digest(token, expected):
        raise PermissionError("local-agent registration is not authorized")


def _agent_row(agent_id: str):
    conn = get_conn()
    try:
        return conn.execute("SELECT * FROM local_agents WHERE id = ?", (agent_id,)).fetchone()
    finally:
        conn.close()


def _require_agent(agent_id: str):
    if not _AGENT_ID_RE.fullmatch(agent_id):
        raise PermissionError("invalid agent id")
    row = _agent_row(agent_id)
    if row is None:
        raise LookupError("agent_not_found")
    token = _bearer_token()
    token_hash = _token_hash(token or "")
    if not secrets.compare_digest(row["token_hash"], token_hash):
        raise PermissionError("invalid local-agent token")
    return row


def init_local_agent_tables() -> None:
    conn = get_conn()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS local_agents (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                version TEXT NOT NULL DEFAULT '1.0',
                capabilities_json TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'offline',
                token_hash TEXT NOT NULL,
                last_seen REAL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS local_agent_jobs (
                id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                arguments_json TEXT NOT NULL DEFAULT '{}',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                trace_id TEXT,
                invocation_id TEXT,
                status TEXT NOT NULL DEFAULT 'queued',
                result_json TEXT,
                error_json TEXT,
                created_at REAL NOT NULL,
                claimed_at REAL,
                lease_until REAL,
                completed_at REAL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_local_agent_jobs_poll "
            "ON local_agent_jobs(agent_id, status, created_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_local_agent_jobs_trace "
            "ON local_agent_jobs(trace_id)"
        )
        conn.commit()
    finally:
        conn.close()


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

    conn = get_conn()
    try:
        agent = conn.execute(
            "SELECT status FROM local_agents WHERE id = ?", (agent_id,)
        ).fetchone()
        if agent is None:
            raise LookupError("agent_not_found")

        job_id = str(uuid4())
        conn.execute(
            """
            INSERT INTO local_agent_jobs (
                id, agent_id, tool_name, arguments_json, metadata_json,
                trace_id, invocation_id, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?)
            """,
            (
                job_id,
                agent_id,
                tool_name,
                _json(dict(arguments)),
                _json(dict(metadata or {})),
                trace_id,
                invocation_id,
                _now(),
            ),
        )
        conn.commit()
        return job_id
    finally:
        conn.close()


def _recover_expired_jobs(conn, agent_id: str) -> None:
    now = _now()
    conn.execute(
        """
        UPDATE local_agent_jobs
        SET status = 'queued', claimed_at = NULL, lease_until = NULL
        WHERE agent_id = ? AND status = 'running'
          AND lease_until IS NOT NULL AND lease_until < ?
        """,
        (agent_id, now),
    )


@local_agent_bp.post("/register")
def register_local_agent():
    try:
        _require_bootstrap()
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 401

    data = request.get_json(silent=True) or {}
    agent_id = str(data.get("agent_id") or f"android-{uuid4().hex[:12]}").strip()
    name = str(data.get("name") or "Alice Pro Android Agent").strip()
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
    token = secrets.token_urlsafe(32)

    conn = get_conn()
    try:
        conn.execute(
            """
            INSERT INTO local_agents (
                id, name, version, capabilities_json, status,
                token_hash, last_seen, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'online', ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                version = excluded.version,
                capabilities_json = excluded.capabilities_json,
                status = 'online',
                token_hash = excluded.token_hash,
                last_seen = excluded.last_seen,
                updated_at = excluded.updated_at
            """,
            (
                agent_id,
                name,
                version,
                _json(capabilities),
                _token_hash(token),
                now,
                now,
                now,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return jsonify(
        {
            "agent": {
                "id": agent_id,
                "name": name,
                "version": version,
                "capabilities": capabilities,
                "status": "online",
            },
            "token": token,
        }
    ), 201


@local_agent_bp.post("/<agent_id>/heartbeat")
def local_agent_heartbeat(agent_id: str):
    try:
        _require_agent(agent_id)
    except LookupError as exc:
        return jsonify({"error": str(exc)}), 404
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 401

    now = _now()
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE local_agents SET status = 'online', last_seen = ?, updated_at = ? WHERE id = ?",
            (now, now, agent_id),
        )
        conn.commit()
    finally:
        conn.close()

    return jsonify({"status": "ok", "agent_id": agent_id, "last_seen": now})


@local_agent_bp.get("/<agent_id>/poll")
def local_agent_poll(agent_id: str):
    try:
        _require_agent(agent_id)
    except LookupError as exc:
        return jsonify({"error": str(exc)}), 404
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 401

    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        _recover_expired_jobs(conn, agent_id)
        now = _now()
        row = conn.execute(
            """
            SELECT * FROM local_agent_jobs
            WHERE agent_id = ? AND status = 'queued'
            ORDER BY created_at ASC
            LIMIT 1
            """,
            (agent_id,),
        ).fetchone()

        conn.execute(
            "UPDATE local_agents SET status = 'online', last_seen = ?, updated_at = ? WHERE id = ?",
            (now, now, agent_id),
        )

        if row is None:
            conn.commit()
            return jsonify({"job": None, "poll_after_seconds": 2})

        lease_until = now + _DEFAULT_LEASE_SECONDS
        conn.execute(
            """
            UPDATE local_agent_jobs
            SET status = 'running', claimed_at = ?, lease_until = ?
            WHERE id = ? AND status = 'queued'
            """,
            (now, lease_until, row["id"]),
        )
        conn.commit()

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
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@local_agent_bp.post("/<agent_id>/jobs/<job_id>/result")
def local_agent_result(agent_id: str, job_id: str):
    try:
        _require_agent(agent_id)
    except LookupError as exc:
        return jsonify({"error": str(exc)}), 404
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 401

    data = request.get_json(silent=True) or {}
    status = str(data.get("status") or "").strip().lower()
    if status not in {"completed", "failed"}:
        return jsonify({"error": "status_must_be_completed_or_failed"}), 400

    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT status FROM local_agent_jobs WHERE id = ? AND agent_id = ?",
            (job_id, agent_id),
        ).fetchone()
        if row is None:
            return jsonify({"error": "job_not_found"}), 404
        if row["status"] in {"completed", "failed"}:
            return jsonify({"status": "ok", "idempotent": True})

        now = _now()
        conn.execute(
            """
            UPDATE local_agent_jobs
            SET status = ?, result_json = ?, error_json = ?, completed_at = ?,
                lease_until = NULL
            WHERE id = ? AND agent_id = ?
            """,
            (
                status,
                _json(data.get("result")),
                _json(data.get("error")),
                now,
                job_id,
                agent_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return jsonify({"status": "ok", "job_id": job_id})


@local_agent_bp.get("/health")
def local_agent_health():
    conn = get_conn()
    try:
        cutoff = _now() - 90
        rows = conn.execute(
            """
            SELECT id, name, version, capabilities_json, status, last_seen, updated_at
            FROM local_agents
            ORDER BY id ASC
            """
        ).fetchall()
    finally:
        conn.close()

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


__all__ = ["enqueue_local_tool_job", "init_local_agent_tables", "local_agent_bp"]
