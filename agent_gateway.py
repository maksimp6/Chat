"""Provider-agnostic gateway for registering and invoking Alice Pro agents.

The gateway intentionally contains no provider SDK dependencies. Adapters can
implement ``AgentHandler`` for local agents, MCP, REST, A2A, or other systems.

The optional A2A client below implements the HTTP+JSON/JSON-RPC transport used
by current A2A specifications. It is deliberately kept as a small stdlib-only
adapter so Android does not inherit an external agent SDK dependency.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Callable, Mapping, MutableMapping, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4
import json


class AgentGatewayError(Exception):
    """Base exception for gateway errors."""


class AgentAlreadyRegistered(AgentGatewayError):
    pass


class AgentNotFound(AgentGatewayError):
    pass


class AgentInvocationError(AgentGatewayError):
    pass


class A2AProtocolError(AgentGatewayError):
    """Raised when a remote A2A endpoint returns an invalid response."""


AgentHandler = Callable[[Mapping[str, Any]], Any]
TokenProvider = Callable[[], Optional[str]]


@dataclass(frozen=True)
class AgentDescriptor:
    """Public metadata used to discover and route work to an agent."""

    agent_id: str
    name: str
    capabilities: tuple[str, ...] = ()
    version: str = "1.0"
    transport: str = "local"
    enabled: bool = True
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class InvocationResult:
    """Stable result envelope returned by the gateway."""

    invocation_id: str
    agent_id: str
    status: str
    result: Any = None
    error: Optional[str] = None
    started_at: str = ""
    finished_at: str = ""


@dataclass(frozen=True)
class A2AClientConfig:
    """Connection settings for an HTTP A2A endpoint."""

    endpoint: str
    timeout_seconds: float = 30.0


class A2AClient:
    """Small dependency-free A2A JSON-RPC client.

    The request shape follows the A2A ``message/send`` JSON-RPC method. The
    endpoint is supplied by the caller, normally from the remote agent's
    Agent Card. Authentication is provided at runtime by ``token_provider`` so
    credentials are never persisted in the gateway object or trace payload.
    """

    def __init__(
        self,
        config: A2AClientConfig,
        token_provider: Optional[TokenProvider] = None,
    ) -> None:
        if not config.endpoint.strip():
            raise ValueError("A2A endpoint must not be empty")
        if config.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._config = config
        self._token_provider = token_provider

    def send_message(self, payload: Mapping[str, Any]) -> Any:
        """Send a ``message/send`` request and return its JSON-RPC result."""

        message = payload.get("message")
        if not isinstance(message, Mapping):
            raise A2AProtocolError("payload.message must be an object")

        params: dict[str, Any] = {"message": dict(message)}
        for key in ("configuration", "metadata"):
            value = payload.get(key)
            if value is not None:
                params[key] = value

        request_id = str(uuid4())
        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "message/send",
                "params": params,
            },
            separators=(",", ":"),
        ).encode("utf-8")

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self._token_provider is not None:
            token = self._token_provider()
            if token:
                headers["Authorization"] = f"Bearer {token}"

        request = Request(
            self._config.endpoint,
            data=body,
            headers=headers,
            method="POST",
        )
        try:
            with urlopen(request, timeout=self._config.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            raise A2AProtocolError(
                f"A2A HTTP error {exc.code}: {exc.reason}"
            ) from exc
        except (URLError, TimeoutError) as exc:
            raise A2AProtocolError(f"A2A transport error: {exc}") from exc

        try:
            response_obj = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise A2AProtocolError("A2A response is not valid JSON") from exc

        if not isinstance(response_obj, Mapping):
            raise A2AProtocolError("A2A response must be a JSON object")
        if response_obj.get("id") != request_id:
            raise A2AProtocolError("A2A response id does not match request id")
        if "error" in response_obj:
            error = response_obj["error"]
            raise AgentInvocationError(_format_a2a_error(error))
        if "result" not in response_obj:
            raise A2AProtocolError("A2A response has neither result nor error")
        return response_obj["result"]

    def handler(self) -> AgentHandler:
        """Return a gateway-compatible handler for ``message/send``."""

        return self.send_message


def _format_a2a_error(error: Any) -> str:
    if isinstance(error, Mapping):
        code = error.get("code")
        message = error.get("message")
        if code is not None and message:
            return f"A2A error {code}: {message}"
        if message:
            return f"A2A error: {message}"
    return "A2A remote agent returned an error"


class AgentGateway:
    """Thread-safe registry and dispatcher for Alice Pro agents."""

    def __init__(self) -> None:
        self._agents: MutableMapping[str, tuple[AgentDescriptor, AgentHandler]] = {}
        self._lock = RLock()

    def register(self, descriptor: AgentDescriptor, handler: AgentHandler) -> None:
        if not descriptor.agent_id.strip():
            raise ValueError("agent_id must not be empty")
        if not callable(handler):
            raise TypeError("handler must be callable")
        with self._lock:
            if descriptor.agent_id in self._agents:
                raise AgentAlreadyRegistered(descriptor.agent_id)
            self._agents[descriptor.agent_id] = (descriptor, handler)

    def unregister(self, agent_id: str) -> None:
        with self._lock:
            if agent_id not in self._agents:
                raise AgentNotFound(agent_id)
            del self._agents[agent_id]

    def get(self, agent_id: str) -> AgentDescriptor:
        with self._lock:
            entry = self._agents.get(agent_id)
            if entry is None:
                raise AgentNotFound(agent_id)
            return entry[0]

    def list_agents(self, capability: Optional[str] = None) -> list[AgentDescriptor]:
        with self._lock:
            descriptors = [descriptor for descriptor, _ in self._agents.values()]
        if capability is not None:
            descriptors = [
                descriptor
                for descriptor in descriptors
                if capability in descriptor.capabilities
            ]
        return sorted(descriptors, key=lambda item: item.agent_id)

    def invoke(self, agent_id: str, payload: Mapping[str, Any]) -> InvocationResult:
        invocation_id = str(uuid4())
        started_at = datetime.now(timezone.utc).isoformat()
        with self._lock:
            entry = self._agents.get(agent_id)
        if entry is None:
            raise AgentNotFound(agent_id)
        descriptor, handler = entry
        if not descriptor.enabled:
            raise AgentInvocationError(f"agent is disabled: {agent_id}")
        try:
            result = handler(payload)
        except Exception as exc:  # preserve a stable envelope for callers
            finished_at = datetime.now(timezone.utc).isoformat()
            return InvocationResult(
                invocation_id=invocation_id,
                agent_id=agent_id,
                status="error",
                error=f"{type(exc).__name__}: {exc}",
                started_at=started_at,
                finished_at=finished_at,
            )
        return InvocationResult(
            invocation_id=invocation_id,
            agent_id=agent_id,
            status="completed",
            result=result,
            started_at=started_at,
            finished_at=datetime.now(timezone.utc).isoformat(),
        )


__all__ = [
    "A2AClient",
    "A2AClientConfig",
    "A2AProtocolError",
    "AgentAlreadyRegistered",
    "AgentDescriptor",
    "AgentGateway",
    "AgentGatewayError",
    "AgentInvocationError",
    "AgentNotFound",
    "InvocationResult",
]
