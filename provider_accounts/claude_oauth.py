"""Optional Claude Console OAuth transport for Logos's existing tool loop.

The official ant CLI owns login and refresh. No Claude.ai session files or
refresh tokens are read here. Nothing is installed, authorized or run on import.
"""

from __future__ import annotations
from core.env import env_get, env_snapshot

import copy
import json
import re
import shutil
import subprocess
import threading
import time
import uuid
from typing import Any

import requests

from agent_runtime.logos_launch_hold import enforce_native_launch_hold
from provider_accounts.claude_catalog import CLAUDE_MODELS
from storage.db import get_messages
from tool_providers.registry import registry

ENDPOINT = "https://api.anthropic.com/v1/messages"
MAX_BYTES = 2 * 1024 * 1024
# Conversation histories kept in this process; the oldest is dropped first.
_MAX_HISTORIES = 256
# The official CLI is asked for a token at most this often; a 401 forgets the cached one at once.
_TOKEN_TTL_SECONDS = 240
_token_cache: dict[str, Any] = {"token": None, "expires_at": 0.0}
_token_lock = threading.Lock()


class ClaudeOAuthError(RuntimeError):
    """Public-safe provider failure; never include CLI output or HTTP bodies."""


def is_claude_model(model_key: Any) -> bool:
    return isinstance(model_key, str) and model_key.startswith("claude-")


def create_local_conversation(trace: Any = None) -> dict[str, str]:
    data = {"id": str(uuid.uuid4())}
    if trace is not None:
        trace.add_event(
            "conversation_created",
            {"provider": "anthropic", "storage": "local", "conversation_id": data["id"]},
        )
    return data


def _cli_output(binary: str, arguments: list[str], credential_environment: dict[str, str]) -> str:
    try:
        credential_process = subprocess.run(
            [binary, *arguments],
            env=credential_environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        if (
            credential_process.returncode
            or credential_process.stderr.strip()
            or len(credential_process.stdout) > 65536
        ):
            raise ClaudeOAuthError("Claude Console OAuth login or refresh required")
        return credential_process.stdout.strip()
    except (OSError, subprocess.SubprocessError, ValueError, TypeError):
        raise ClaudeOAuthError("Claude Console OAuth credentials unavailable") from None


def _cli_json(
    binary: str, arguments: list[str], credential_environment: dict[str, str]
) -> dict[str, Any]:
    try:
        data = json.loads(_cli_output(binary, arguments, credential_environment))
        if not isinstance(data, dict):
            raise TypeError
        return data
    except (OSError, subprocess.SubprocessError, ValueError, TypeError):
        raise ClaudeOAuthError("Claude Console OAuth credentials unavailable") from None


def _fetch_access_token() -> str:
    """Capture the official CLI's token privately; no shell or API-key fallback."""
    binary = shutil.which("ant")
    if not binary:
        raise ClaudeOAuthError("Official ant CLI is required for Claude Console OAuth")
    profile = env_get("LOGOS_CLAUDE_OAUTH_PROFILE", "logos")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", profile):
        raise ClaudeOAuthError("Invalid Claude Console OAuth profile")
    credential_environment = dict(env_snapshot())
    for name in (
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_PROFILE",
        "ANTHROPIC_BASE_URL",
        "CLAUDE_CODE_OAUTH_TOKEN",
    ):
        credential_environment.pop(name, None)
    arguments = ["--profile", profile]
    config = _cli_json(binary, [*arguments, "profile", "get"], credential_environment)
    auth = config.get("authentication") or {}
    if (
        not isinstance(auth, dict)
        or auth.get("type") != "user_oauth"
        or config.get("base_url", ENDPOINT.removesuffix("/v1/messages"))
        not in ("", "https://api.anthropic.com")
        or config.get("console_url", "https://platform.claude.com")
        not in ("", "https://platform.claude.com")
    ):
        raise ClaudeOAuthError("A first-party Claude Console OAuth profile is required")
    # Request only the access token; the official CLI owns refresh credentials.
    token = _cli_output(
        binary, [*arguments, "auth", "print-credentials", "--access-token"], credential_environment
    )
    if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_.~-]{8,8192}", token):
        raise ClaudeOAuthError("Claude Console OAuth inference authorization required")
    return token


def resolve_access_token() -> str:
    with _token_lock:
        if _token_cache["token"] and time.monotonic() < _token_cache["expires_at"]:
            return _token_cache["token"]
    token = _fetch_access_token()
    with _token_lock:
        _token_cache.update(token=token, expires_at=time.monotonic() + _TOKEN_TTL_SECONDS)
    return token


def forget_access_token() -> None:
    with _token_lock:
        _token_cache.update(token=None, expires_at=0.0)


def normalize_response(data: Any) -> dict[str, Any]:
    """Adapt Messages to the response shape consumed by the native Logos loop."""
    if (
        not isinstance(data, dict)
        or data.get("type") != "message"
        or data.get("role") != "assistant"
        or not isinstance(data.get("id"), str)
        or not data["id"]
        or not isinstance(data.get("content"), list)
        or not isinstance(data.get("model"), str)
    ):
        raise ClaudeOAuthError("Invalid Claude response envelope")
    output: list[dict[str, Any]] = []
    tool_ids: set[str] = set()
    for block in data["content"]:
        if not isinstance(block, dict):
            raise ClaudeOAuthError("Invalid Claude content")
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            output.append(
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": block["text"]}],
                }
            )
        elif block.get("type") == "tool_use":
            identity, name, arguments = block.get("id"), block.get("name"), block.get("input")
            if (
                not isinstance(identity, str)
                or not identity
                or identity in tool_ids
                or not isinstance(name, str)
                or not name
                or not isinstance(arguments, dict)
            ):
                raise ClaudeOAuthError("Invalid Claude tool call")
            tool_ids.add(identity)
            output.append(
                {
                    "type": "function_call",
                    "call_id": identity,
                    "name": name,
                    "arguments": json.dumps(arguments),
                }
            )
        elif block.get("type") not in {"thinking", "redacted_thinking"}:
            raise ClaudeOAuthError("Unsupported Claude content")
    stop = data.get("stop_reason")
    if (stop == "tool_use") != bool(tool_ids):
        raise ClaudeOAuthError("Invalid Claude tool stop reason")
    if stop in {"end_turn", "stop_sequence"} and not any(
        item["type"] == "message" and item["content"][0]["text"].strip() for item in output
    ):
        raise ClaudeOAuthError("Claude returned no assistant answer")
    # Truncated/refused output is never executed as a tool batch or a success.
    if stop not in {"end_turn", "tool_use", "stop_sequence"}:
        output = [item for item in output if item["type"] != "function_call"]
    usage = data.get("usage")
    if not isinstance(usage, dict) or any(
        type(usage.get(key)) is not int or usage[key] < 0
        for key in ("input_tokens", "output_tokens")
    ):
        raise ClaudeOAuthError("Invalid Claude usage")
    for field in ("cache_creation_input_tokens", "cache_read_input_tokens"):
        if type(usage.get(field, 0)) is not int or usage.get(field, 0) < 0:
            raise ClaudeOAuthError("Invalid Claude cache usage")
    normalized_usage = dict(usage)
    normalized_usage["total_tokens"] = sum(
        usage.get(key, 0)
        for key in (
            "input_tokens",
            "output_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
        )
    )
    return {
        "id": data["id"],
        "model": data["model"],
        "provider": "anthropic",
        "status": "completed"
        if stop in {"end_turn", "tool_use", "stop_sequence"}
        else "incomplete",
        "incomplete_details": None
        if stop in {"end_turn", "tool_use", "stop_sequence"}
        else {"reason": str(stop)},
        "output": output,
        "usage": normalized_usage,
    }


class ClaudeOAuthTransport:
    """Per-client/per-conversation history; tool execution stays in LogosClient."""

    def __init__(self) -> None:
        self._histories: dict[tuple[Any, Any], list[dict[str, Any]]] = {}

    def ask(
        self,
        message: Any,
        model_key: str,
        conversation_id: Any = None,
        params: Any = None,
        trace: Any = None,
    ) -> dict[str, Any]:
        enforce_native_launch_hold()
        params = dict(params or {})
        if model_key not in CLAUDE_MODELS:
            raise ClaudeOAuthError("Claude model is not configured")
        for unsupported in ("previous_response_id", "vector_store_ids", "text", "reasoning"):
            if params.get(unsupported):
                raise ClaudeOAuthError("Unsupported Claude request option: " + unsupported)
        key = (conversation_id, model_key)
        prior = self._histories.get(key)
        messages = copy.deepcopy(prior or [])
        if "input" in params:
            if not prior or not isinstance(params["input"], list):
                raise ClaudeOAuthError("Claude continuation has no matching request")
            if any(not isinstance(item, dict) for item in params["input"]):
                raise ClaudeOAuthError("Invalid Claude continuation input")
            outputs = [
                item for item in params["input"] if item.get("type") == "function_call_output"
            ]
            expected = [b["id"] for b in messages[-1]["content"] if b.get("type") == "tool_use"]
            if not expected or [item.get("call_id") for item in outputs] != expected:
                raise ClaudeOAuthError("Claude tool result IDs do not match")
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": item["call_id"],
                            "content": str(item.get("output", "")),
                        }
                        for item in outputs
                    ],
                }
            )
        else:
            messages = [
                {"role": item["role"], "content": item["text"]}
                for item in (get_messages(conversation_id) if conversation_id else [])
                if item.get("role") in {"user", "assistant"} and item.get("text")
            ]
            if not isinstance(message, str) or not message.strip():
                raise ClaudeOAuthError("Claude requires a text message")
            if not messages or messages[-1] != {"role": "user", "content": message}:
                messages.append({"role": "user", "content": message})
        limit = params.get("max_output_tokens", 8192)
        if type(limit) is not int or not 1 <= limit <= 128000:
            raise ClaudeOAuthError("Invalid Claude max_output_tokens")
        payload = {"model": model_key, "messages": messages, "max_tokens": limit}
        if params.get("instructions"):
            payload["system"] = params["instructions"]
        for name in ("temperature", "top_p"):
            if name in params:
                value = params[name]
                if type(value) not in (int, float) or not 0 <= value <= 1:
                    raise ClaudeOAuthError("Invalid Claude sampling parameter")
                payload[name] = value
        if "temperature" in payload and "top_p" in payload:
            raise ClaudeOAuthError("Choose one Claude sampling parameter")
        if params.get("stream"):
            raise ClaudeOAuthError("Claude OAuth streaming is not implemented")
        tools_config = params.get("tools_config") or {}
        if not isinstance(tools_config, dict):
            raise ClaudeOAuthError("Invalid Claude tools_config")
        for config in tools_config.values():
            if isinstance(config, dict) and config.get("enabled"):
                raise ClaudeOAuthError("Claude hosted tools are not implemented")
        tools = params.get("tools") or []
        if tools:
            if any(tool.get("type") != "function" for tool in tools):
                raise ClaudeOAuthError("Claude transport supports local function tools only")
            payload["tools"] = [
                {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "input_schema": tool.get("parameters", {"type": "object", "properties": {}}),
                }
                for tool in tools
            ]
        if len(json.dumps(payload).encode()) > MAX_BYTES:
            raise ClaudeOAuthError("Claude request exceeds size limit")
        token = resolve_access_token()
        step = len(trace.trace.get("api_requests", [])) + 1 if trace is not None else 1
        if trace is not None:
            trace.register_sensitive_value(token)
            trace.add_api_request(payload, step_index=step)
            trace.add_event(
                "provider_selected",
                {"provider": "anthropic", "auth": "console_oauth", "model": model_key},
            )
        started = time.time()
        deadline = time.monotonic() + 90
        try:
            with (
                requests.Session() as session,
                session.post(
                    ENDPOINT,
                    json=payload,
                    headers={
                        "Authorization": "Bearer " + token,
                        "anthropic-version": "2023-06-01",
                        "anthropic-beta": "oauth-2025-04-20",
                    },
                    timeout=(10, 90),
                    stream=True,
                    allow_redirects=False,
                ) as response,
            ):
                if response.status_code == 401:
                    forget_access_token()
                if response.status_code != 200:
                    raise ClaudeOAuthError(f"Claude API HTTP {response.status_code}")
                raw = bytearray()
                for chunk in response.iter_content(65536):
                    if time.monotonic() > deadline:
                        raise ClaudeOAuthError("Claude response deadline exceeded")
                    raw.extend(chunk)
                    if len(raw) > MAX_BYTES:
                        raise ClaudeOAuthError("Claude response exceeds size limit")
                data = json.loads(raw)
            normalized = normalize_response(data)
            if normalized["model"] != model_key and not normalized["model"].startswith(
                model_key + "-"
            ):
                raise ClaudeOAuthError("Claude response model does not match the request")
        except (requests.RequestException, ValueError, OSError):
            raise ClaudeOAuthError("Claude API transport or response failed") from None
        if normalized["status"] == "completed":
            self._histories[key] = messages + [{"role": "assistant", "content": data["content"]}]
            while len(self._histories) > _MAX_HISTORIES:
                self._histories.pop(next(iter(self._histories)))
        if trace is not None:
            trace.add_response(normalized, step_index=step, start_timestamp=started)
        if callable(params.get("on_provider_response")):
            params["on_provider_response"](normalized)
        return normalized

    def ask_with_mcp(
        self,
        message: Any,
        model_key: str,
        conversation_id: Any = None,
        params: Any = None,
        trace: Any = None,
        *,
        execution_policy: Any = None,
    ) -> dict[str, Any]:
        enforce_native_launch_hold()
        params = dict(params or {})
        categories = params.get("active_tool_categories")
        if categories is None:
            categories = list(registry.get_available_categories())
        # Tool schemas come from the registry only; a client cannot add definitions of its own.
        tools: list[dict[str, Any]] = []
        for category in categories:
            for tool in registry.get_tools_by_category(category):
                definition = registry.get_universal_definition(tool["name"])
                if execution_policy is None or (
                    definition is not None and execution_policy.allows_tool(definition)
                ):
                    tools.append(tool)
        if execution_policy is not None:
            tools = [
                tool
                for tool in tools
                if (
                    (definition := registry.get_universal_definition(tool["name"])) is not None
                    and execution_policy.allows_tool(definition)
                )
            ]
        params["tools"] = list({tool["name"]: tool for tool in tools}.values())
        return self.ask(message, model_key, conversation_id, params, trace)
