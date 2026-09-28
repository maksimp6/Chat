"""Provider-agnostic gateway for Alice Pro agents."""

from __future__ import annotations
from collections import deque
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Callable, Iterator, Mapping, MutableMapping, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen
from uuid import uuid4
import json
import time


class AgentGatewayError(Exception):
    pass


class AgentAlreadyRegistered(AgentGatewayError):
    pass


class AgentNotFound(AgentGatewayError):
    pass


class AgentInvocationError(AgentGatewayError):
    pass


class AgentPermissionError(AgentGatewayError):
    pass


class AgentApprovalRequired(AgentGatewayError):
    pass


class AgentRateLimitError(AgentGatewayError):
    pass


class AgentCircuitOpenError(AgentGatewayError):
    pass


class A2AProtocolError(AgentGatewayError):
    pass


AgentHandler = Callable[[Mapping[str, Any]], Any]
TokenProvider = Callable[[], Optional[str]]


@dataclass(frozen=True)
class AgentDescriptor:
    agent_id: str
    name: str
    capabilities: tuple[str, ...] = ()
    version: str = "1.0"
    transport: str = "local"
    enabled: bool = True
    metadata: Mapping[str, Any] = field(default_factory=dict)
    risk_level: str = "low"
    requires_approval: bool = False
    allowed_users: tuple[str, ...] = ()


@dataclass(frozen=True)
class InvocationResult:
    invocation_id: str
    agent_id: str
    status: str
    result: Any = None
    error: Optional[str] = None
    attempts: int = 0
    started_at: str = ""
    finished_at: str = ""


@dataclass(frozen=True)
class A2AClientConfig:
    endpoint: str = ""
    agent_card_url: str = ""
    timeout_seconds: float = 30.0


class A2AClient:
    def __init__(
        self, config: A2AClientConfig, token_provider: Optional[TokenProvider] = None
    ) -> None:
        if not config.endpoint.strip() and not config.agent_card_url.strip():
            raise ValueError("A2A endpoint or agent_card_url must be configured")
        if config.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._config = config
        self._token_provider = token_provider
        self._endpoint = config.endpoint.strip()
        self._endpoint_lock = RLock()

    def _headers(self, *, accept: str) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": accept}
        if self._token_provider is not None:
            token = self._token_provider()
            if token:
                headers["Authorization"] = "Bearer " + token
        return headers

    def _load_agent_card(self) -> Mapping[str, Any]:
        card_url = self._config.agent_card_url.strip()
        if not card_url:
            raise A2AProtocolError("A2A agent card URL is not configured")
        req = Request(
            card_url,
            headers=self._headers(accept="application/json"),
            method="GET",
        )
        try:
            with urlopen(req, timeout=self._config.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            raise A2AProtocolError(f"A2A Agent Card HTTP error {exc.code}: {exc.reason}") from exc
        except (URLError, TimeoutError) as exc:
            raise A2AProtocolError(f"A2A Agent Card transport error: {exc}") from exc
        try:
            card = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise A2AProtocolError("A2A Agent Card is not valid JSON") from exc
        if not isinstance(card, Mapping):
            raise A2AProtocolError("A2A Agent Card must be a JSON object")
        return card

    @staticmethod
    def _extract_card_endpoint(card: Mapping[str, Any], card_url: str) -> str:
        for key in ("endpoint", "agent_endpoint", "agentEndpoint"):
            value = card.get(key)
            if isinstance(value, str) and value.strip():
                return urljoin(card_url, value.strip())
        transports = card.get("transports")
        if isinstance(transports, list):
            for item in transports:
                if not isinstance(item, Mapping):
                    continue
                kind = str(item.get("protocol") or item.get("name") or "").lower()
                if kind == "a2a":
                    value = item.get("endpoint") or item.get("url")
                    if isinstance(value, str) and value.strip():
                        return urljoin(card_url, value.strip())
        capabilities = card.get("capabilities")
        if isinstance(capabilities, Mapping):
            a2a = capabilities.get("a2a")
            if isinstance(a2a, Mapping):
                value = a2a.get("endpoint") or a2a.get("url")
                if isinstance(value, str) and value.strip():
                    return urljoin(card_url, value.strip())
        raise A2AProtocolError("A2A Agent Card does not define an endpoint")

    def _resolve_endpoint(self) -> str:
        if self._endpoint:
            return self._endpoint
        with self._endpoint_lock:
            if self._endpoint:
                return self._endpoint
            card_url = self._config.agent_card_url.strip()
            card = self._load_agent_card()
            self._endpoint = self._extract_card_endpoint(card, card_url)
            return self._endpoint

    @staticmethod
    def _parse_sse(raw: str) -> Iterator[Any]:
        data_lines: list[str] = []
        normalized_raw = raw.replace("\r\n", "\n").replace("\r", "\n")
        for line in normalized_raw.split("\n"):
            if not line:
                if data_lines:
                    event = "\n".join(data_lines)
                    data_lines = []
                    if event == "[DONE]":
                        return
                    try:
                        yield json.loads(event)
                    except json.JSONDecodeError:
                        yield event
                continue
            if line.startswith(":"):
                continue
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
        if data_lines:
            event = "\n".join(data_lines)
            if event == "[DONE]":
                return
            try:
                yield json.loads(event)
            except json.JSONDecodeError:
                yield event

    def _send_rpc(
        self,
        payload: Mapping[str, Any],
        *,
        accept: str,
    ) -> tuple[str, Any]:
        message = payload.get("message")
        if not isinstance(message, Mapping):
            raise A2AProtocolError("payload.message must be an object")
        request_id = str(uuid4())
        params = {"message": dict(message)}
        for key in ("configuration", "metadata"):
            if payload.get(key) is not None:
                params[key] = payload[key]
        body = json.dumps(
            {"jsonrpc": "2.0", "id": request_id, "method": "message/send", "params": params},
            separators=(",", ":"),
        ).encode()
        req = Request(
            self._resolve_endpoint(),
            data=body,
            headers=self._headers(accept=accept),
            method="POST",
        )
        try:
            with urlopen(req, timeout=self._config.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            raise A2AProtocolError(f"A2A HTTP error {exc.code}: {exc.reason}") from exc
        except (URLError, TimeoutError) as exc:
            raise A2AProtocolError(f"A2A transport error: {exc}") from exc
        if accept == "text/event-stream":
            return request_id, raw
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise A2AProtocolError("A2A response is not valid JSON") from exc
        return request_id, obj

    def send_message(self, payload: Mapping[str, Any]) -> Any:
        request_id, obj = self._send_rpc(payload, accept="application/json")
        if not isinstance(obj, Mapping):
            raise A2AProtocolError("A2A response must be a JSON object")
        if obj.get("id") != request_id:
            raise A2AProtocolError("A2A response id does not match request id")
        if "error" in obj:
            raise AgentInvocationError(_format_a2a_error(obj["error"]))
        if "result" not in obj:
            raise A2AProtocolError("A2A response has neither result nor error")
        return obj["result"]

    def stream_message(self, payload: Mapping[str, Any]) -> Iterator[Any]:
        request_id, raw = self._send_rpc(payload, accept="text/event-stream")
        if not isinstance(raw, str):
            raise A2AProtocolError("A2A SSE response must be a text stream")
        for event in self._parse_sse(raw):
            if not isinstance(event, Mapping):
                yield event
                continue
            if event.get("id") not in (None, request_id):
                raise A2AProtocolError("A2A SSE event id does not match request id")
            if "error" in event:
                raise AgentInvocationError(_format_a2a_error(event["error"]))
            yield event.get("result", event)

    def handler(self) -> AgentHandler:
        return self.send_message


def cloudru_iam_token_provider(
    db: Any,
    decrypt: Callable[[str], str],
    *,
    iam_endpoint: Optional[str] = None,
    timeout_seconds: float = 20.0,
) -> TokenProvider:
    """Resolve Cloud.ru IAM credentials from the backend store and mint tokens."""
    from cloudru_iam import CloudRuIamClient, CloudRuIamError
    from provider_credentials import get_cloudru_iam_credentials

    credentials = get_cloudru_iam_credentials(db, decrypt)
    if not credentials:
        raise AgentInvocationError("Cloud.ru IAM credentials are not configured")
    client = CloudRuIamClient(
        key_id=credentials["key_id"],
        key_secret=credentials["key_secret"],
        endpoint=iam_endpoint,
        timeout=timeout_seconds,
    )

    def _provider() -> Optional[str]:
        try:
            return client.access_token()
        except CloudRuIamError as exc:
            raise A2AProtocolError("Cloud.ru IAM token exchange failed") from exc

    return _provider


class RESTAgentClient:
    def __init__(
        self,
        endpoint: str,
        token_provider: Optional[TokenProvider] = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        if not endpoint.strip():
            raise ValueError("REST endpoint must not be empty")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.endpoint = endpoint
        self.timeout_seconds = timeout_seconds
        self.token_provider = token_provider

    def send(self, payload: Mapping[str, Any]) -> Any:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.token_provider is not None:
            token = self.token_provider()
            if token:
                headers["Authorization"] = "Bearer " + token
        req = Request(
            self.endpoint,
            data=json.dumps(dict(payload), separators=(",", ":")).encode(),
            headers=headers,
            method="POST",
        )
        try:
            with urlopen(req, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            raise AgentInvocationError(f"REST HTTP error {exc.code}: {exc.reason}") from exc
        except (URLError, TimeoutError) as exc:
            raise AgentInvocationError(f"REST transport error: {exc}") from exc
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AgentInvocationError("REST agent response is not valid JSON") from exc

    def handler(self) -> AgentHandler:
        return self.send


def _format_a2a_error(error: Any) -> str:
    if isinstance(error, Mapping):
        if error.get("code") is not None and error.get("message"):
            return f"A2A error {error['code']}: {error['message']}"
        if error.get("message"):
            return f"A2A error: {error['message']}"
    return "A2A remote agent returned an error"


@dataclass
class _CircuitState:
    failures: int = 0
    opened_at: float | None = None
    calls: deque[float] = field(default_factory=deque)


class AgentGateway:
    def __init__(
        self,
        *,
        default_timeout_seconds: float = 30.0,
        default_max_retries: int = 1,
        rate_limit_per_agent: int = 60,
        rate_window_seconds: float = 60.0,
        circuit_failure_threshold: int = 3,
        circuit_reset_seconds: float = 30.0,
    ) -> None:
        self.default_timeout_seconds = float(default_timeout_seconds)
        self.default_max_retries = int(default_max_retries)
        self.rate_limit_per_agent = int(rate_limit_per_agent)
        self.rate_window_seconds = float(rate_window_seconds)
        self.circuit_failure_threshold = int(circuit_failure_threshold)
        self.circuit_reset_seconds = float(circuit_reset_seconds)
        if self.default_timeout_seconds <= 0 or self.default_max_retries < 0:
            raise ValueError("invalid timeout/retry configuration")
        if self.rate_limit_per_agent <= 0 or self.rate_window_seconds <= 0:
            raise ValueError("invalid rate limit configuration")
        if self.circuit_failure_threshold <= 0 or self.circuit_reset_seconds <= 0:
            raise ValueError("invalid circuit configuration")
        self._agents: MutableMapping[str, tuple[AgentDescriptor, AgentHandler]] = {}
        self._circuits: MutableMapping[str, _CircuitState] = {}
        self._lock = RLock()

    def register(self, descriptor: AgentDescriptor, handler: AgentHandler) -> None:
        if not descriptor.agent_id.strip():
            raise ValueError("agent_id must not be empty")
        if not callable(handler):
            raise TypeError("handler must be callable")
        if descriptor.risk_level not in {"low", "medium", "high", "critical"}:
            raise ValueError("unsupported risk_level")
        with self._lock:
            if descriptor.agent_id in self._agents:
                raise AgentAlreadyRegistered(descriptor.agent_id)
            self._agents[descriptor.agent_id] = (descriptor, handler)
            self._circuits[descriptor.agent_id] = _CircuitState()

    def unregister(self, agent_id: str) -> None:
        with self._lock:
            if agent_id not in self._agents:
                raise AgentNotFound(agent_id)
            del self._agents[agent_id]
            self._circuits.pop(agent_id, None)

    def get(self, agent_id: str) -> AgentDescriptor:
        with self._lock:
            entry = self._agents.get(agent_id)
            if entry is None:
                raise AgentNotFound(agent_id)
            return entry[0]

    def list_agents(self, capability: Optional[str] = None) -> list[AgentDescriptor]:
        with self._lock:
            items = [descriptor for descriptor, _ in self._agents.values()]
        if capability is not None:
            items = [a for a in items if capability in a.capabilities]
        return sorted(items, key=lambda a: a.agent_id)

    def _authorize(
        self, descriptor: AgentDescriptor, user_id: Optional[str], approved: bool
    ) -> None:
        if descriptor.allowed_users and (
            user_id is None or user_id not in descriptor.allowed_users
        ):
            raise AgentPermissionError(f"agent access denied: {descriptor.agent_id}")
        if descriptor.requires_approval and not approved:
            raise AgentApprovalRequired(f"agent approval required: {descriptor.agent_id}")

    def _check_rate(self, agent_id: str, now: float) -> None:
        state = self._circuits[agent_id]
        cutoff = now - self.rate_window_seconds
        while state.calls and state.calls[0] < cutoff:
            state.calls.popleft()
        if len(state.calls) >= self.rate_limit_per_agent:
            raise AgentRateLimitError(f"agent rate limit exceeded: {agent_id}")
        state.calls.append(now)

    def _check_circuit(self, agent_id: str, now: float) -> None:
        state = self._circuits[agent_id]
        if state.opened_at is None:
            return
        if now - state.opened_at < self.circuit_reset_seconds:
            raise AgentCircuitOpenError(f"agent circuit is open: {agent_id}")
        state.opened_at = None
        state.failures = 0

    def _record_success(self, agent_id: str) -> None:
        state = self._circuits[agent_id]
        state.failures = 0
        state.opened_at = None

    def _record_failure(self, agent_id: str, now: float) -> None:
        state = self._circuits[agent_id]
        state.failures += 1
        if state.failures >= self.circuit_failure_threshold:
            state.opened_at = now

    @staticmethod
    def _trace_event(trace: Any, event_type: str, payload: dict[str, Any]) -> None:
        if trace is not None and hasattr(trace, "add_event"):
            trace.add_event(event_type, payload)

    @staticmethod
    def _trace_error(trace: Any, source: str, message: str, invocation_id: str) -> None:
        if trace is not None and hasattr(trace, "record_error"):
            trace.record_error(source, message, call_id=invocation_id)

    @staticmethod
    def _invoke_once(
        handler: AgentHandler, payload: Mapping[str, Any], timeout_seconds: float
    ) -> Any:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(handler, payload)
            try:
                return future.result(timeout=timeout_seconds)
            except FutureTimeoutError as exc:
                future.cancel()
                raise AgentInvocationError(
                    f"agent invocation timed out after {timeout_seconds:.2f}s"
                ) from exc

    def invoke(
        self,
        agent_id: str,
        payload: Mapping[str, Any],
        *,
        user_id: Optional[str] = None,
        approved: bool = False,
        trace: Any = None,
        timeout_seconds: Optional[float] = None,
        max_retries: Optional[int] = None,
    ) -> InvocationResult:
        if not isinstance(payload, Mapping):
            raise TypeError("payload must be an object")
        invocation_id = str(uuid4())
        started_at = datetime.now(timezone.utc)
        timeout = (
            self.default_timeout_seconds if timeout_seconds is None else float(timeout_seconds)
        )
        retries = self.default_max_retries if max_retries is None else int(max_retries)
        if timeout <= 0 or retries < 0:
            raise ValueError("invalid timeout/retry configuration")
        with self._lock:
            entry = self._agents.get(agent_id)
            if entry is None:
                raise AgentNotFound(agent_id)
            descriptor, handler = entry
            if not descriptor.enabled:
                raise AgentInvocationError(f"agent is disabled: {agent_id}")
            try:
                self._authorize(descriptor, user_id, approved)
                now = time.monotonic()
                self._check_circuit(agent_id, now)
                self._check_rate(agent_id, now)
            except (
                AgentPermissionError,
                AgentApprovalRequired,
                AgentCircuitOpenError,
                AgentRateLimitError,
            ) as exc:
                self._trace_error(trace, f"agent:{agent_id}", str(exc), invocation_id)
                return InvocationResult(
                    invocation_id,
                    agent_id,
                    "error",
                    error=str(exc),
                    attempts=0,
                    started_at=started_at.isoformat(),
                    finished_at=datetime.now(timezone.utc).isoformat(),
                )
        self._trace_event(
            trace,
            "agent_invocation_started",
            {
                "agent_id": agent_id,
                "invocation_id": invocation_id,
                "transport": descriptor.transport,
            },
        )
        last_error = None
        attempts = 0
        for attempt in range(1, retries + 2):
            attempts = attempt
            try:
                result = self._invoke_once(handler, payload, timeout)
                with self._lock:
                    self._record_success(agent_id)
                self._trace_event(
                    trace,
                    "agent_invocation_completed",
                    {
                        "agent_id": agent_id,
                        "invocation_id": invocation_id,
                        "attempts": attempts,
                        "status": "completed",
                    },
                )
                return InvocationResult(
                    invocation_id,
                    agent_id,
                    "completed",
                    result=result,
                    attempts=attempts,
                    started_at=started_at.isoformat(),
                    finished_at=datetime.now(timezone.utc).isoformat(),
                )
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                with self._lock:
                    self._record_failure(agent_id, time.monotonic())
                self._trace_error(trace, f"agent:{agent_id}", last_error, invocation_id)
                self._trace_event(
                    trace,
                    "agent_invocation_retry",
                    {
                        "agent_id": agent_id,
                        "invocation_id": invocation_id,
                        "attempt": attempt,
                        "will_retry": attempt <= retries,
                    },
                )
                if attempt <= retries:
                    continue
        return InvocationResult(
            invocation_id,
            agent_id,
            "error",
            error=last_error,
            attempts=attempts,
            started_at=started_at.isoformat(),
            finished_at=datetime.now(timezone.utc).isoformat(),
        )

    def route(
        self,
        capability: str,
        payload: Mapping[str, Any],
        *,
        preferred_agent_id: Optional[str] = None,
        user_id: Optional[str] = None,
        approved: bool = False,
        trace: Any = None,
    ) -> InvocationResult:
        candidates = self.list_agents(capability)
        if preferred_agent_id:
            preferred = self.get(preferred_agent_id)
            candidates = [preferred] + [a for a in candidates if a.agent_id != preferred.agent_id]
        candidates = [a for a in candidates if a.enabled]
        if not candidates:
            raise AgentNotFound(f"no enabled agent for capability: {capability}")
        errors = []
        for descriptor in candidates:
            result = self.invoke(
                descriptor.agent_id, payload, user_id=user_id, approved=approved, trace=trace
            )
            if result.status == "completed":
                return result
            errors.append(result.error or "agent invocation failed")
        raise AgentInvocationError("; ".join(errors) or "no agent completed the request")


__all__ = [
    "A2AClient",
    "A2AClientConfig",
    "A2AProtocolError",
    "RESTAgentClient",
    "cloudru_iam_token_provider",
    "AgentAlreadyRegistered",
    "AgentApprovalRequired",
    "AgentCircuitOpenError",
    "AgentDescriptor",
    "AgentGateway",
    "AgentGatewayError",
    "AgentInvocationError",
    "AgentNotFound",
    "AgentPermissionError",
    "AgentRateLimitError",
    "InvocationResult",
]
