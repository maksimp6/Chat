"""Client/runtime for a protected Alice Pro Local Tool Agent."""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Mapping, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from tool_registry import registry
from universal_tool_platform import UniversalToolCall, UniversalToolExecutor

logger = logging.getLogger("local_tool_agent")


def _request(
    method: str,
    url: str,
    *,
    token: Optional[str] = None,
    payload: Optional[Mapping[str, Any]] = None,
    timeout: float = 15,
) -> Any:
    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"

    request = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        if exc.code in {401, 403}:
            raise PermissionError(
                f"agent gateway authentication failed: HTTP {exc.code}"
            ) from exc
        raise RuntimeError(
            f"agent gateway HTTP error {exc.code}: {exc.reason}"
        ) from exc
    except (URLError, TimeoutError) as exc:
        raise ConnectionError(f"agent gateway unavailable: {exc}") from exc

    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("agent gateway returned invalid JSON") from exc


def register_agent(
    gateway_url: str,
    bootstrap_token: str,
    *,
    agent_id: Optional[str] = None,
    name: str = "Alice Pro Android Agent",
    capabilities: Optional[list[str]] = None,
    version: str = "1.0",
) -> str:
    """Register an agent and return a newly issued runtime token as JSON."""
    gateway_url = gateway_url.rstrip("/")
    if not gateway_url:
        raise ValueError("gateway_url is required")
    if not bootstrap_token:
        raise ValueError("bootstrap_token is required")

    data = _request(
        "POST",
        f"{gateway_url}/api/local-agents/register",
        token=bootstrap_token,
        payload={
            "agent_id": agent_id,
            "name": name,
            "version": version,
            "capabilities": capabilities or [],
        },
    )
    if not isinstance(data, Mapping) or not data.get("token") or not data.get("agent"):
        raise RuntimeError("invalid agent registration response")
    return json.dumps(
        {"token": str(data["token"]), "agent": data["agent"]},
        ensure_ascii=False,
        separators=(",", ":"),
    )


class LocalToolAgent:
    """Worker that pulls and executes queued local tools."""

    def __init__(
        self,
        gateway_url: str,
        agent_id: str,
        token: str,
        *,
        poll_interval: float = 2.0,
        stop_event: Optional[threading.Event] = None,
    ) -> None:
        self.gateway_url = gateway_url.rstrip("/")
        self.agent_id = agent_id
        self.token = token
        self.poll_interval = max(0.5, float(poll_interval))
        self.stop_event = stop_event or threading.Event()

    def run_forever(self) -> None:
        poll_url = (
            f"{self.gateway_url}/api/local-agents/{self.agent_id}/poll"
        )
        while not self.stop_event.is_set():
            try:
                response = _request(
                    "GET", poll_url, token=self.token, timeout=20
                )
                job = response.get("job") if isinstance(response, Mapping) else None
                if not job:
                    delay = float(
                        response.get("poll_after_seconds", self.poll_interval)
                    )
                    self.stop_event.wait(max(0.5, delay))
                    continue
                self._execute_job(job)
            except PermissionError:
                logger.error("Local agent authorization failed; stopping")
                return
            except (ConnectionError, RuntimeError) as exc:
                logger.warning("Local agent gateway unavailable: %s", exc)
                self.stop_event.wait(min(self.poll_interval * 2, 30.0))
            except Exception:
                logger.exception("Unexpected local agent worker error")
                self.stop_event.wait(self.poll_interval)

    def _execute_job(self, job: Mapping[str, Any]) -> None:
        job_id = str(job.get("id") or "")
        tool_name = str(job.get("tool_name") or "")
        arguments = job.get("arguments")

        if not job_id or not tool_name or not isinstance(arguments, Mapping):
            self._submit(job_id, "failed", {"error": "invalid_job"})
            return

        started = time.monotonic()
        try:
            call = UniversalToolCall(
                tool_name=tool_name,
                arguments=dict(arguments),
                transport="local_agent",
                call_id=job_id,
                trace_id=job.get("trace_id"),
                invocation_id=job.get("invocation_id"),
                user_id=(job.get("metadata") or {}).get("user_id"),
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
            "tool": tool_name,
            "agent_id": self.agent_id,
            "duration_ms": round(
                (time.monotonic() - started) * 1000, 2
            ),
        }
        self._submit(job_id, status, result)

    def _submit(
        self,
        job_id: str,
        status: str,
        result: Mapping[str, Any],
    ) -> None:
        if not job_id:
            return
        _request(
            "POST",
            (
                f"{self.gateway_url}/api/local-agents/"
                f"{self.agent_id}/jobs/{job_id}/result"
            ),
            token=self.token,
            payload={"status": status, "result": dict(result)},
            timeout=20,
        )


def start_agent(
    gateway_url: str,
    bootstrap_token: str,
    *,
    agent_id: Optional[str] = None,
    name: str = "Alice Pro Android Agent",
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
    agent_token = str(registration["token"])
    descriptor = registration["agent"]
    worker = LocalToolAgent(
        gateway_url,
        str(descriptor["id"]),
        agent_token,
    )
    return worker, agent_token


__all__ = ["LocalToolAgent", "register_agent", "start_agent"]
