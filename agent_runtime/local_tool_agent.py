"""Client/runtime for the shared Logos Local Tool Agent gateway."""

from __future__ import annotations

import fcntl
import json
import hashlib
import logging
import os
from pathlib import Path
import tempfile
import threading
import time
from http.client import HTTPException
from typing import Any, Mapping, Optional
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from tool_providers.registry import registry
from tool_providers.universal_tool_platform import (
    UniversalToolCall,
    UniversalToolExecutor,
)

logger = logging.getLogger("logos.agent_runtime.local_tool_agent")

# Renew well inside the gateway's 30 s job lease so a slow tool is not requeued and run twice.
_LEASE_RENEW_SECONDS = 10.0
_PROTOCOL_VERSION = 2


class AgentProtocolError(RuntimeError):
    """Fail closed before execution when the peer cannot fence claims."""


class ResultDeliveryBlocked(RuntimeError):
    """Keep the outbox and stop polling until its conflict is reconciled."""


class GatewayHTTPError(RuntimeError):
    def __init__(self, status: int):
        self.status = status
        super().__init__(f"agent gateway HTTP error {status}")


def _check_protocol(response: Any) -> None:
    if (
        not isinstance(response, Mapping)
        or response.get("protocol_version") != _PROTOCOL_VERSION
    ):
        raise AgentProtocolError("Local agent gateway requires protocol version 2")


def _request(
    method: str,
    url: str,
    *,
    token: Optional[str] = None,
    payload: Optional[Mapping[str, Any]] = None,
    timeout: float = 15,
) -> Any:
    body = None
    headers = {
        "Accept": "application/json",
        "X-Logos-Agent-Protocol": str(_PROTOCOL_VERSION),
    }
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        headers["Content-Type"] = "application/json"

    request = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        raise GatewayHTTPError(exc.code) from exc
    except (HTTPException, OSError) as exc:
        raise ConnectionError(f"agent gateway unavailable: {exc}") from exc

    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("agent gateway returned invalid JSON") from exc


def register_agent(
    gateway_url: str,
    bootstrap_token: str = "",
    *,
    agent_id: Optional[str] = None,
    name: str = "Logos Android Agent",
    capabilities: Optional[list[str]] = None,
    version: str = "1.0",
) -> str:
    """Register an agent and return its descriptor as JSON. Legacy token arguments are ignored."""
    gateway_url = gateway_url.rstrip("/")
    if not gateway_url:
        raise ValueError("gateway_url is required")

    data = _request(
        "POST",
        f"{gateway_url}/api/local-agents/register",
        payload={
            "protocol_version": _PROTOCOL_VERSION,
            "agent_id": agent_id,
            "name": name,
            "version": version,
            "capabilities": capabilities or [],
        },
    )
    _check_protocol(data)
    if not isinstance(data.get("agent"), Mapping) or not data["agent"].get("id"):
        raise RuntimeError("invalid agent registration response")
    return json.dumps(
        {"agent": data["agent"]},
        ensure_ascii=False,
        separators=(",", ":"),
    )


class LocalToolAgent:
    """Worker that pulls and executes queued local tools."""

    def __init__(
        self,
        gateway_url: str,
        agent_id: str,
        token: str = "",
        *,
        poll_interval: float = 2.0,
        stop_event: Optional[threading.Event] = None,
        outbox_dir: Optional[str | Path] = None,
    ) -> None:
        self.gateway_url = gateway_url.rstrip("/")
        self.agent_id = agent_id
        self.poll_interval = max(0.5, float(poll_interval))
        self.stop_event = stop_event or threading.Event()
        identity = hashlib.sha256(
            json.dumps([self.gateway_url, self.agent_id], separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()
        state_root = Path(
            os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state"
        )
        self.outbox_dir = (
            Path(outbox_dir)
            if outbox_dir is not None
            else (state_root / "logos" / "local-tool-agent" / "outbox" / identity)
        )

    def run_forever(self) -> None:
        try:
            self.outbox_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            self.outbox_dir.chmod(0o700)
        except OSError:
            logger.exception("Local agent outbox is unavailable; no jobs will execute")
            return
        try:
            lock_descriptor = self._acquire_outbox_lock()
        except BlockingIOError:
            logger.error(
                "Another local agent worker owns this outbox; no jobs will be polled"
            )
            return
        except OSError:
            logger.exception(
                "Local agent outbox lock is unavailable; no jobs will execute"
            )
            return
        try:
            self._sync_outbox_directory()
            self._run_locked()
        except OSError:
            logger.exception(
                "Local agent stopped because durable outbox storage failed"
            )
        finally:
            try:
                fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
            finally:
                os.close(lock_descriptor)

    def _acquire_outbox_lock(self) -> int:
        descriptor = os.open(
            self.outbox_dir / ".worker.lock",
            os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            os.fchmod(descriptor, 0o600)
            os.set_inheritable(descriptor, False)
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            os.close(descriptor)
            raise
        return descriptor

    def _run_locked(self) -> None:
        poll_url = f"{self.gateway_url}/api/local-agents/{self.agent_id}/poll"
        protocol_checked = False
        while not self.stop_event.is_set():
            try:
                if not protocol_checked:
                    _check_protocol(
                        _request("GET", f"{self.gateway_url}/api/local-agents/health")
                    )
                    protocol_checked = True
                if not self._drain_outbox():
                    return
                response = _request("GET", poll_url, timeout=20)
                _check_protocol(response)
                job = response.get("job") if isinstance(response, Mapping) else None
                if not job:
                    delay = float(
                        response.get("poll_after_seconds", self.poll_interval)
                    )
                    self.stop_event.wait(max(0.5, delay))
                    continue
                self._run_with_lease(job)
            except (AgentProtocolError, ResultDeliveryBlocked):
                logger.exception(
                    "Local agent stopped; claims/outbox need reconciliation"
                )
                return
            except (ConnectionError, RuntimeError) as exc:
                logger.warning("Local agent gateway unavailable: %s", exc)
                self.stop_event.wait(min(self.poll_interval * 2, 30.0))
            except Exception:
                logger.exception("Unexpected local agent worker error")
                # A disk/write failure after a side effect must never lead to another poll.
                return

    def _run_with_lease(self, job: Mapping[str, Any]) -> None:
        if (
            not isinstance(job, Mapping)
            or not isinstance(job.get("id"), str)
            or not job["id"]
            or not isinstance(job.get("claim_token"), str)
            or not job["claim_token"]
            or not isinstance(job.get("attempt"), int)
            or isinstance(job["attempt"], bool)
            or job["attempt"] < 1
            or not isinstance(job.get("arguments"), Mapping)
            or not job.get("tool_name")
        ):
            raise AgentProtocolError("Gateway returned an invalid fenced job")
        # Verify durable storage BEFORE asking for start permission or causing side effects.
        # A surviving intent without a result is uncertain and is never executed on restart.
        self._persist_record(job, "intent")
        stop = threading.Event()
        renewer = threading.Thread(
            target=self._renew_leases, args=(stop, job), daemon=True
        )
        renewer.start()
        try:
            if not self._start_job(job):
                return
            self._execute_job(job)
        finally:
            stop.set()
            renewer.join(timeout=5)

    def _renew_leases(self, stop: threading.Event, job: Mapping[str, Any]) -> None:
        heartbeat_url = f"{self.gateway_url}/api/local-agents/{self.agent_id}/heartbeat"
        while not stop.wait(_LEASE_RENEW_SECONDS):
            try:
                response = _request(
                    "POST",
                    heartbeat_url,
                    timeout=10,
                    payload={"job_id": job["id"], "claim_token": job["claim_token"]},
                )
                self._check_ack(response, job, "ok")
            except GatewayHTTPError as exc:
                if exc.status >= 500 or exc.status in {408, 429}:
                    logger.warning(
                        "Local agent lease renewal is temporarily unavailable"
                    )
                    continue
                logger.warning(
                    "Local agent claim cannot be renewed; its outcome may be uncertain"
                )
                return
            except AgentProtocolError:
                logger.warning(
                    "Local agent claim cannot be renewed; its outcome may be uncertain"
                )
                return
            except (ConnectionError, RuntimeError) as exc:
                logger.warning("Local agent lease renewal failed: %s", exc)

    @staticmethod
    def _check_ack(response: Any, job: Mapping[str, Any], status: str) -> None:
        _check_protocol(response)
        if (
            response.get("status") != status
            or response.get("job_id") != job["id"]
            or response.get("claim_token") != job["claim_token"]
        ):
            raise AgentProtocolError("Gateway did not acknowledge the expected claim")

    def _start_job(self, job: Mapping[str, Any]) -> bool:
        while not self.stop_event.is_set():
            try:
                response = _request(
                    "POST",
                    f"{self.gateway_url}/api/local-agents/{self.agent_id}/jobs/{job['id']}/start",
                    payload={"claim_token": job["claim_token"]},
                    timeout=20,
                )
                self._check_ack(response, job, "running")
                return not self.stop_event.is_set()
            except GatewayHTTPError as exc:
                if exc.status < 500 and exc.status not in {408, 429}:
                    raise ResultDeliveryBlocked(
                        "Start claim rejected; intent retained without execution"
                    ) from exc
            except AgentProtocolError:
                raise
            except (ConnectionError, RuntimeError):
                # A lost start ACK is retried for the same claim, never interpreted as permission.
                logger.warning("Local agent awaits a valid start acknowledgement")
            self.stop_event.wait(self.poll_interval)
        return False

    def _execute_job(self, job: Mapping[str, Any]) -> None:
        job_id = str(job.get("id") or "")
        tool_name = str(job.get("tool_name") or "")
        arguments = job.get("arguments")

        if not job_id or not tool_name or not isinstance(arguments, Mapping):
            raise AgentProtocolError("Invalid job after start acknowledgement")

        started = time.monotonic()
        try:
            call = UniversalToolCall(
                tool_name=tool_name,
                arguments=dict(arguments),
                transport="local_agent",
                call_id=job_id,
                trace_id=job.get("trace_id"),
                invocation_id=job.get("invocation_id"),
                approved=True,
                metadata={
                    "source": "local_tool_agent",
                    "agent_id": self.agent_id,
                    **dict(job.get("metadata") or {}),
                },
            )
            result = UniversalToolExecutor(registry).execute(call)
            status = "completed" if result.get("success") else "failed"
        except Exception as exc:
            result = {
                "success": False,
                "data": None,
                "error": f"{type(exc).__name__}: {exc}",
            }
            status = "failed"

        result["metadata"] = {
            **dict(result.get("metadata") or {}),
            "tool": tool_name,
            "agent_id": self.agent_id,
            "job_id": job_id,
            "claim_attempt": job["attempt"],
            "duration_ms": round((time.monotonic() - started) * 1000, 2),
        }
        self._persist_record(job, "result", status=status, result=result)
        self._drain_outbox()

    def _sync_outbox_directory(self) -> None:
        descriptor = os.open(
            self.outbox_dir, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        )
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _persist_record(self, job, state: str, *, status=None, result=None) -> None:
        record = {
            "protocol_version": _PROTOCOL_VERSION,
            "gateway_url": self.gateway_url,
            "agent_id": self.agent_id,
            "job_id": job["id"],
            "claim_token": job["claim_token"],
            "attempt": job["attempt"],
            "state": state,
            "status": status,
            "result": result,
        }
        body = json.dumps(
            record, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        )
        path = self.outbox_dir / (
            hashlib.sha256(job["id"].encode("utf-8")).hexdigest() + ".json"
        )
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.outbox_dir, delete=False
            ) as stream:
                temporary = Path(stream.name)
                os.fchmod(stream.fileno(), 0o600)
                stream.write(body)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            temporary = None
            self._sync_outbox_directory()
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _drain_outbox(self) -> bool:
        for path in sorted(self.outbox_dir.glob("*.json")):
            record = json.loads(path.read_text(encoding="utf-8"))
            if (
                not isinstance(record, dict)
                or record.get("protocol_version") != _PROTOCOL_VERSION
                or record.get("gateway_url") != self.gateway_url
                or record.get("agent_id") != self.agent_id
                or not isinstance(record.get("job_id"), str)
                or not record["job_id"]
                or not isinstance(record.get("claim_token"), str)
                or not record["claim_token"]
            ):
                raise ResultDeliveryBlocked(
                    "Outbox identity is invalid; record retained"
                )
            if record.get("state") != "result":
                raise ResultDeliveryBlocked(
                    "Unresolved execution intent: outcome unknown; automatic execution/retry is forbidden"
                )
            while not self.stop_event.is_set():
                try:
                    response = _request(
                        "POST",
                        f"{self.gateway_url}/api/local-agents/{self.agent_id}/jobs/{record['job_id']}/result",
                        payload={
                            "claim_token": record["claim_token"],
                            "status": record["status"],
                            "result": record["result"],
                        },
                        timeout=20,
                    )
                    self._check_ack(
                        response,
                        {"id": record["job_id"], "claim_token": record["claim_token"]},
                        "ok",
                    )
                    path.unlink()
                    self._sync_outbox_directory()
                    break
                except GatewayHTTPError as exc:
                    if exc.status < 500 and exc.status not in {408, 429}:
                        raise ResultDeliveryBlocked(
                            "Result claim rejected; outbox retained"
                        ) from exc
                except AgentProtocolError:
                    raise
                except (ConnectionError, RuntimeError):
                    logger.warning(
                        "Local agent result delivery pending; no new job will be polled"
                    )
                self.stop_event.wait(self.poll_interval)
            else:
                return False
        return True


def start_agent(
    gateway_url: str,
    bootstrap_token: str = "",
    *,
    agent_id: Optional[str] = None,
    name: str = "Logos Android Agent",
    capabilities: Optional[list[str]] = None,
    version: str = "1.0",
) -> tuple[LocalToolAgent, str]:
    registration = json.loads(
        register_agent(
            gateway_url,
            bootstrap_token,
            agent_id=agent_id,
            name=name,
            capabilities=capabilities,
            version=version,
        )
    )
    descriptor = registration["agent"]
    worker = LocalToolAgent(
        gateway_url,
        str(descriptor["id"]),
    )
    return worker, ""


__all__ = ["LocalToolAgent", "register_agent", "start_agent"]
