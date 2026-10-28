from __future__ import annotations

from typing import Any, Callable, Optional

from agent_runtime.gateway import AgentGateway
from conversations.ownership import (
    check_access,
    get_owned_conversation,
    list_owned_conversations,
)
from storage.db import get_messages
from tool_providers.filesystem import grep_search, list_directory, read_file
from invocation.api import get_invocation_status, get_invocation_trace
from sessions.manager import get_session
from tool_providers.registry import registry
from tool_providers.universal_tool_platform import tool_call_requires_approval

from mcp_server.auth import _auth_mode
from mcp_server.protocol import MCP_PATH, SUPPORTED_PROTOCOL_VERSIONS, _server_info


def _tool(
    name: str,
    title: str,
    description: str,
    input_schema: dict[str, Any],
    handler: Callable[[dict[str, Any], Optional[str]], dict[str, Any]],
) -> tuple[dict[str, Any], Callable[[dict[str, Any], Optional[str]], dict[str, Any]]]:
    meta = {
        "openai/toolInvocation/invoking": f"{title}…",
        "openai/toolInvocation/invoked": f"{title}: готово",
    }
    descriptor = {
        "name": name,
        "title": title,
        "description": description,
        "inputSchema": input_schema,
        "securitySchemes": [],
        "_meta": meta,
    }
    return descriptor, handler


def _system_status(_arguments: dict[str, Any], _user: Optional[str]) -> dict[str, Any]:
    from core.config import ALL_MODELS

    categories = registry.get_available_categories()
    return {
        "status": "ok",
        "server": _server_info(),
        "mcp": {
            "endpoint": MCP_PATH,
            "transport": "streamable-http",
            "protocol_versions": sorted(SUPPORTED_PROTOCOL_VERSIONS, reverse=True),
            "auth_mode": _auth_mode(),
        },
        "models": {
            "count": len(ALL_MODELS),
            "available": sorted(ALL_MODELS.keys()),
        },
        "local_tools": {
            "category_count": len(categories),
            "tool_count": sum(len(names) for names in categories.values()),
        },
    }


# One gateway for the process: a gateway built per call would list only its own, empty registry.
_AGENT_GATEWAY = AgentGateway()


def _agents(_arguments: dict[str, Any], _user: Optional[str]) -> dict[str, Any]:
    gateway = _AGENT_GATEWAY
    agents = []
    for agent in gateway.list_agents():
        agents.append(
            {
                "agent_id": agent.agent_id,
                "name": agent.name,
                "capabilities": list(agent.capabilities),
                "version": agent.version,
                "transport": agent.transport,
                "enabled": agent.enabled,
                "metadata": dict(agent.metadata),
            }
        )
    return {"agents": agents}


def _session(arguments: dict[str, Any], user: Optional[str]) -> dict[str, Any]:
    session_id = str(arguments.get("session_id") or "").strip()
    session = get_session(session_id) if session_id else None
    if not session:
        raise LookupError("Session not found")
    return {
        "session": {
            "id": session["id"],
            "status": session["status"],
            "created_at": session["created_at"],
            "updated_at": session["updated_at"],
            "completed_at": session.get("completed_at"),
        }
    }


def _invocation(arguments: dict[str, Any], user: Optional[str]) -> dict[str, Any]:
    invocation_id = str(arguments.get("invocation_id") or "").strip()
    status = get_invocation_status(invocation_id) if invocation_id else None
    if not status:
        raise LookupError("Invocation not found")
    return {"invocation": status}


def _trace(arguments: dict[str, Any], user: Optional[str]) -> dict[str, Any]:
    invocation_id = str(arguments.get("invocation_id") or "").strip()
    status = get_invocation_status(invocation_id) if invocation_id else None
    if not status:
        raise LookupError("Invocation not found")
    trace = get_invocation_trace(invocation_id)
    if trace is None:
        raise LookupError("Invocation trace not found")
    return {"invocation_id": invocation_id, "trace": trace}


def _bridge_wrapper(handler):
    def wrapped(arguments: dict[str, Any], cfg: Optional[dict[str, Any]] = None):
        context = (cfg or {}).get("_universal_context") or {}
        return handler(arguments, context.get("user_id"))

    return wrapped


def _require_conversation_user(user: Optional[str]) -> str:
    from economy.treasury_identity import get_current_owner_id

    return str(user or get_current_owner_id())


def _conversations(_arguments: dict[str, Any], user: Optional[str]) -> dict[str, Any]:
    owner = _require_conversation_user(user)
    return {"conversations": list_owned_conversations(owner)}


def _conversation(arguments: dict[str, Any], user: Optional[str]) -> dict[str, Any]:
    owner = _require_conversation_user(user)
    conversation_id = str(arguments.get("conversation_id") or "").strip()
    if not conversation_id:
        raise ValueError("conversation_id is required")
    conversation = get_owned_conversation(conversation_id, owner)
    if conversation is None:
        raise LookupError("Conversation not found")
    return {"conversation": conversation}


def _conversation_messages(
    arguments: dict[str, Any], user: Optional[str]
) -> dict[str, Any]:
    owner = _require_conversation_user(user)
    conversation_id = str(arguments.get("conversation_id") or "").strip()
    if not conversation_id:
        raise ValueError("conversation_id is required")
    if not check_access(conversation_id, owner):
        raise LookupError("Conversation not found")
    return {
        "conversation_id": conversation_id,
        "messages": get_messages(conversation_id),
    }


def _execution(arguments: dict[str, Any], user: Optional[str]) -> dict[str, Any]:
    owner = _require_conversation_user(user)
    result = _invocation(arguments, owner)
    return {"execution": result["invocation"]}


def _execution_trace(arguments: dict[str, Any], user: Optional[str]) -> dict[str, Any]:
    owner = _require_conversation_user(user)
    return _trace(arguments, owner)


def _project_list(args: dict, _user: Optional[str] = None) -> dict:
    return list_directory({"path": args.get("path") or "."})


def _project_read(args: dict, _user: Optional[str] = None) -> dict:
    path = str(args.get("path") or "").strip()
    if not path:
        raise ValueError("path is required")
    result = read_file(
        {
            "path": path,
            "offset": args.get("offset", 0),
            "length": min(int(args.get("length", 65536)), 65536),
        }
    )
    if result.get("error"):
        raise ValueError(result["error"])
    return result


def _project_search(args: dict, _user: Optional[str] = None) -> dict:
    query = str(args.get("query") or "").strip()
    if not query:
        raise ValueError("query is required")
    return grep_search(
        {
            "query": query,
            "file_pattern": args.get("file_pattern") or "*.py",
            "max_matches": min(int(args.get("max_matches", 50)), 50),
        }
    )


def _register_project_read_tools() -> None:
    tools = [
        (
            "logos_list_project_files",
            "List project files",
            "List files and directories inside the Logos project.",
            {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": [],
                "additionalProperties": False,
            },
            _project_list,
        ),
        (
            "logos_read_project_file",
            "Read project file",
            "Read a bounded UTF-8 file from the Logos project.",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "minLength": 1},
                    "offset": {"type": "integer", "minimum": 0},
                    "length": {"type": "integer", "minimum": 1, "maximum": 65536},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            _project_read,
        ),
        (
            "logos_search_project",
            "Search project code",
            "Search text inside the Logos project.",
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "minLength": 1},
                    "file_pattern": {"type": "string"},
                    "max_matches": {"type": "integer", "minimum": 1, "maximum": 50},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            _project_search,
        ),
    ]
    for name, title, description, input_schema, handler in tools:
        registry.register(
            "chatgpt_project",
            name,
            {
                "title": title,
                "description": description,
                "inputSchema": input_schema,
                "outputSchema": {"type": "object"},
                "capabilities": ["project", "mcp"],
                "risk_level": "medium",
                "read_only": True,
                "requires_approval": False,
                "supported_transports": ["mcp"],
                "executor": {"type": "local"},
                "func": _bridge_wrapper(handler),
            },
        )


_register_project_read_tools()


def _register_conversation_tools() -> None:
    tools = [
        (
            "logos_list_conversations",
            "List conversations",
            "List conversations in the local Logos application.",
            {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
            _conversations,
        ),
        (
            "logos_get_conversation",
            "Get conversation",
            "Read one conversation in the local Logos application.",
            {
                "type": "object",
                "properties": {"conversation_id": {"type": "string", "minLength": 1}},
                "required": ["conversation_id"],
                "additionalProperties": False,
            },
            _conversation,
        ),
        (
            "logos_get_conversation_messages",
            "Get conversation messages",
            "Read messages and persisted ExecutionTrace data for one conversation.",
            {
                "type": "object",
                "properties": {"conversation_id": {"type": "string", "minLength": 1}},
                "required": ["conversation_id"],
                "additionalProperties": False,
            },
            _conversation_messages,
        ),
        (
            "logos_get_execution",
            "Get execution",
            "Read one local execution through the user-facing execution abstraction.",
            {
                "type": "object",
                "properties": {"invocation_id": {"type": "string", "minLength": 1}},
                "required": ["invocation_id"],
                "additionalProperties": False,
            },
            _execution,
        ),
        (
            "logos_get_execution_trace",
            "Get execution trace",
            "Read sanitized ExecutionTrace for one local execution.",
            {
                "type": "object",
                "properties": {"invocation_id": {"type": "string", "minLength": 1}},
                "required": ["invocation_id"],
                "additionalProperties": False,
            },
            _execution_trace,
        ),
    ]
    for name, title, description, input_schema, handler in tools:
        registry.register(
            "chatgpt_conversation",
            name,
            {
                "title": title,
                "description": description,
                "inputSchema": input_schema,
                "outputSchema": {"type": "object"},
                "capabilities": ["conversation", "execution", "mcp"],
                "risk_level": "low",
                "read_only": True,
                "requires_approval": False,
                "supported_transports": ["mcp"],
                "executor": {"type": "local"},
                "func": _bridge_wrapper(handler),
            },
        )


_register_conversation_tools()


def _register_bridge_tools() -> None:
    bridge_tools = [
        (
            "logos_get_system_status",
            "System status",
            "Read-only Logos runtime, MCP, model and local-tool status.",
            {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
            _system_status,
        ),
        (
            "logos_list_agents",
            "List agents",
            "List AI agents currently registered in the Logos Agent Gateway.",
            {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
            _agents,
        ),
        (
            "logos_get_session",
            "Get session",
            "Read-only lifecycle status for one Logos runtime session.",
            {
                "type": "object",
                "properties": {"session_id": {"type": "string", "minLength": 1}},
                "required": ["session_id"],
                "additionalProperties": False,
            },
            _session,
        ),
        (
            "logos_get_invocation",
            "Get invocation",
            "Read-only status and safe metadata for one Logos invocation.",
            {
                "type": "object",
                "properties": {"invocation_id": {"type": "string", "minLength": 1}},
                "required": ["invocation_id"],
                "additionalProperties": False,
            },
            _invocation,
        ),
        (
            "logos_get_invocation_trace",
            "Get invocation trace",
            "Read-only persisted ExecutionTrace for one Logos invocation.",
            {
                "type": "object",
                "properties": {"invocation_id": {"type": "string", "minLength": 1}},
                "required": ["invocation_id"],
                "additionalProperties": False,
            },
            _trace,
        ),
    ]
    for name, title, description, input_schema, handler in bridge_tools:
        registry.register(
            "chatgpt_bridge",
            name,
            {
                "title": title,
                "description": description,
                "inputSchema": input_schema,
                "outputSchema": {"type": "object"},
                "capabilities": ["read", "mcp"],
                "risk_level": "low",
                "read_only": True,
                "requires_approval": False,
                "supported_transports": ["mcp"],
                "executor": {"type": "local"},
                "func": _bridge_wrapper(handler),
            },
        )


_register_bridge_tools()


def _security_schemes() -> list[dict[str, Any]]:
    return [{"type": "noauth"}]


def _tools_list() -> list[dict[str, Any]]:
    schemes = _security_schemes()
    result = []
    for definition in registry.get_universal_definitions("mcp"):
        item = {
            "name": definition["name"],
            "title": definition.get("title") or definition["name"],
            "description": definition.get("description", ""),
            "inputSchema": definition.get("inputSchema")
            or definition.get("input_schema")
            or {},
            "outputSchema": definition.get("outputSchema")
            or definition.get("output_schema")
            or {"type": "object"},
            "securitySchemes": schemes,
        }
        meta = dict(definition.get("metadata") or {})
        meta.update(
            {
                "risk_level": definition.get("risk_level"),
                "read_only": definition.get("read_only"),
                "requires_approval": tool_call_requires_approval(definition),
                "capabilities": definition.get("capabilities") or [],
                "openai/toolInvocation/invoking": f"{item['title']}…",
                "openai/toolInvocation/invoked": f"{item['title']}: готово",
                "securitySchemes": schemes,
            }
        )
        item["_meta"] = meta
        result.append(item)
    return result
