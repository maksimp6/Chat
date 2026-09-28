"""Provider-neutral cloud tools exposed through the universal tool registry."""

from __future__ import annotations

from typing import Any

import environment_manager
from cloud.base import CloudProviderError
from cloud.registry import ensure_default_providers, resolve_provider_name
from runtime_tools import ssh_runtime_exec


def _provider(args: dict[str, Any]):
    name = resolve_provider_name(args)
    return ensure_default_providers().get(name)


def _schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _result(fn):
    def wrapper(args: dict[str, Any], cfg: dict | None = None):
        try:
            return fn(args, cfg)
        except CloudProviderError as exc:
            return {
                "success": False,
                "error": str(exc),
                "metadata": {
                    "phase": "execution",
                    "provider_code": exc.code,
                    "provider_http_status": exc.http_status,
                },
            }

    return wrapper


@_result
def cloud_capabilities(args: dict[str, Any], cfg: dict | None = None) -> dict[str, Any]:
    provider = _provider(args)
    return provider.capabilities()


@_result
def cloud_resources_list(args: dict[str, Any], cfg: dict | None = None) -> dict[str, Any]:
    provider = _provider(args)
    return provider.list_resources(
        service=str(args.get("service") or ""),
        resource_type=(
            str(args.get("resource_type")) if isinstance(args.get("resource_type"), str) else None
        ),
        filters=dict(args.get("filters") or {}),
    )


@_result
def cloud_resources_get(args: dict[str, Any], cfg: dict | None = None) -> dict[str, Any]:
    provider = _provider(args)
    return provider.get_resource(
        resource_type=str(args.get("resource_type") or ""),
        resource_id=str(args.get("resource_id") or ""),
        service=(str(args.get("service")) if isinstance(args.get("service"), str) else None),
    )


@_result
def cloud_compute(args: dict[str, Any], cfg: dict | None = None) -> dict[str, Any]:
    provider = _provider(args)
    return provider.compute(
        operation=str(args.get("operation") or ""),
        instance_id=(str(args.get("instance_id")) if args.get("instance_id") is not None else None),
        extra=dict(args.get("extra") or {}),
    )


@_result
def cloud_logs_query(args: dict[str, Any], cfg: dict | None = None) -> dict[str, Any]:
    provider = _provider(args)
    return provider.query_logs(
        query=str(args.get("query") or ""),
        service=(str(args.get("service")) if isinstance(args.get("service"), str) else None),
        limit=int(args.get("limit") or 100),
    )


@_result
def cloud_metrics_query(args: dict[str, Any], cfg: dict | None = None) -> dict[str, Any]:
    provider = _provider(args)
    return provider.query_metrics(
        query=str(args.get("query") or ""),
        service=(str(args.get("service")) if isinstance(args.get("service"), str) else None),
        window=(str(args.get("window")) if isinstance(args.get("window"), str) else None),
        limit=int(args.get("limit") or 100),
    )


@_result
def cloud_backup(args: dict[str, Any], cfg: dict | None = None) -> dict[str, Any]:
    provider = _provider(args)
    return provider.backup(
        operation=str(args.get("operation") or ""),
        resource_id=(str(args.get("resource_id")) if args.get("resource_id") is not None else None),
        options=dict(args.get("options") or {}),
    )


@_result
def cloud_costs_summary(args: dict[str, Any], cfg: dict | None = None) -> dict[str, Any]:
    provider = _provider(args)
    return provider.costs_summary(
        period=(str(args.get("period")) if isinstance(args.get("period"), str) else None),
        group_by=(str(args.get("group_by")) if isinstance(args.get("group_by"), str) else None),
    )


def cloud_ssh_exec(args: dict[str, Any], cfg: dict | None = None) -> dict[str, Any]:
    runtime_args = {
        "target": args.get("target"),
        "command": args.get("command"),
        "timeout_seconds": args.get("timeout_seconds"),
    }
    return ssh_runtime_exec(runtime_args, cfg)


def _environment_owner_id(cfg: dict | None) -> str | None:
    context = cfg.get("_universal_context") if isinstance(cfg, dict) else None
    call = context.get("call") if isinstance(context, dict) else None
    identity = getattr(call, "user_id", None) if call is not None else None
    return str(identity).strip() if identity is not None and str(identity).strip() else None


def _environment_error(exc: Exception) -> dict[str, Any]:
    return {"success": False, "error": str(exc)}


def cloud_environment_create(args: dict[str, Any], cfg: dict | None = None) -> dict[str, Any]:
    """Provision an isolated Alice sandbox on Cloud.ru (replaces local subprocess/SSH)."""
    try:
        return environment_manager.create_environment(
            str(args.get("branch") or ""),
            args.get("commit_sha"),
            _environment_owner_id(cfg),
            adapter="cloudru",
            ttl_seconds=args.get("ttl_seconds"),
        )
    except (ValueError, CloudProviderError) as exc:
        return _environment_error(exc)


def cloud_environment_start(args: dict[str, Any], cfg: dict | None = None) -> dict[str, Any]:
    try:
        return environment_manager.start_environment(
            str(args.get("environment_id") or ""), _environment_owner_id(cfg)
        )
    except (KeyError, ValueError, CloudProviderError) as exc:
        return _environment_error(exc)


def cloud_environment_stop(args: dict[str, Any], cfg: dict | None = None) -> dict[str, Any]:
    try:
        return environment_manager.stop_environment(
            str(args.get("environment_id") or ""), _environment_owner_id(cfg)
        )
    except (KeyError, ValueError, CloudProviderError) as exc:
        return _environment_error(exc)


def cloud_environment_delete(args: dict[str, Any], cfg: dict | None = None) -> dict[str, Any]:
    try:
        return environment_manager.delete_environment(
            str(args.get("environment_id") or ""), _environment_owner_id(cfg)
        )
    except (KeyError, ValueError, CloudProviderError) as exc:
        return _environment_error(exc)


def cloud_environment_exec(args: dict[str, Any], cfg: dict | None = None) -> dict[str, Any]:
    try:
        return environment_manager.execute_environment(
            str(args.get("environment_id") or ""),
            str(args.get("command") or ""),
            _environment_owner_id(cfg),
            timeout_seconds=float(args.get("timeout_seconds") or 30.0),
        )
    except (KeyError, ValueError, CloudProviderError) as exc:
        return _environment_error(exc)


def _tool(
    fn,
    *,
    read_only: bool,
    requires_approval: bool,
    schema: dict[str, Any],
    title: str,
    description: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "title": title,
        "description": description,
        "parameters": schema,
        "capabilities": ["cloud"],
        "risk_level": "low" if read_only else "high",
        "read_only": read_only,
        "requires_approval": requires_approval,
        "supported_transports": ["responses_api", "local_agent", "mcp"],
        "executor": {"type": "local"},
        "metadata": metadata or {},
        "func": fn,
    }


_PROVIDER = {
    "provider": {"type": "string", "minLength": 1, "maxLength": 64},
}


CLOUD_TOOLS = {
    "cloud.capabilities": _tool(
        cloud_capabilities,
        read_only=True,
        requires_approval=False,
        schema=_schema(_PROVIDER, []),
        title="Cloud Capabilities",
        description="Return provider-neutral cloud service capabilities.",
    ),
    "cloud.resources.list": _tool(
        cloud_resources_list,
        read_only=True,
        requires_approval=False,
        schema=_schema(
            {
                **_PROVIDER,
                "service": {"type": "string", "minLength": 1, "maxLength": 64},
                "resource_type": {"anyOf": [{"type": "string", "maxLength": 64}, {"type": "null"}]},
                "filters": {"anyOf": [{"type": "object"}, {"type": "null"}]},
            },
            ["service"],
        ),
        title="Cloud Resources List",
        description="List cloud resources by service.",
    ),
    "cloud.resources.get": _tool(
        cloud_resources_get,
        read_only=True,
        requires_approval=False,
        schema=_schema(
            {
                **_PROVIDER,
                "service": {"anyOf": [{"type": "string", "maxLength": 64}, {"type": "null"}]},
                "resource_type": {"type": "string", "minLength": 1, "maxLength": 64},
                "resource_id": {"type": "string", "minLength": 1, "maxLength": 256},
            },
            ["resource_type", "resource_id"],
        ),
        title="Cloud Resource Get",
        description="Get one cloud resource by ID.",
    ),
    "cloud.compute.list": _tool(
        cloud_compute,
        read_only=True,
        requires_approval=False,
        schema=_schema(
            {
                **_PROVIDER,
                "operation": {"type": "string", "const": "list"},
                "instance_id": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                "extra": {"anyOf": [{"type": "object"}, {"type": "null"}]},
            },
            ["operation"],
        ),
        title="Cloud Compute List",
        description="List compute instances through infrastructure APIs.",
    ),
    "cloud.compute.status": _tool(
        cloud_compute,
        read_only=True,
        requires_approval=False,
        schema=_schema(
            {
                **_PROVIDER,
                "operation": {"type": "string", "const": "status"},
                "instance_id": {"type": "string", "minLength": 1, "maxLength": 256},
                "extra": {"anyOf": [{"type": "object"}, {"type": "null"}]},
            },
            ["operation", "instance_id"],
        ),
        title="Cloud Compute Status",
        description="Get compute instance status through infrastructure APIs.",
    ),
    "cloud.compute.start": _tool(
        cloud_compute,
        read_only=False,
        requires_approval=True,
        schema=_schema(
            {
                **_PROVIDER,
                "operation": {"type": "string", "const": "start"},
                "instance_id": {"type": "string", "minLength": 1, "maxLength": 256},
                "extra": {"anyOf": [{"type": "object"}, {"type": "null"}]},
            },
            ["operation", "instance_id"],
        ),
        title="Cloud Compute Start",
        description="Start a compute instance via provider API (confirmation required).",
    ),
    "cloud.compute.stop": _tool(
        cloud_compute,
        read_only=False,
        requires_approval=True,
        schema=_schema(
            {
                **_PROVIDER,
                "operation": {"type": "string", "const": "stop"},
                "instance_id": {"type": "string", "minLength": 1, "maxLength": 256},
                "extra": {"anyOf": [{"type": "object"}, {"type": "null"}]},
            },
            ["operation", "instance_id"],
        ),
        title="Cloud Compute Stop",
        description="Stop a compute instance via provider API (confirmation required).",
    ),
    "cloud.compute.reboot": _tool(
        cloud_compute,
        read_only=False,
        requires_approval=True,
        schema=_schema(
            {
                **_PROVIDER,
                "operation": {"type": "string", "const": "reboot"},
                "instance_id": {"type": "string", "minLength": 1, "maxLength": 256},
                "extra": {"anyOf": [{"type": "object"}, {"type": "null"}]},
            },
            ["operation", "instance_id"],
        ),
        title="Cloud Compute Reboot",
        description="Reboot a compute instance via provider API (confirmation required).",
    ),
    "cloud.logs.query": _tool(
        cloud_logs_query,
        read_only=True,
        requires_approval=False,
        schema=_schema(
            {
                **_PROVIDER,
                "query": {"type": "string", "minLength": 1, "maxLength": 8000},
                "service": {"anyOf": [{"type": "string", "maxLength": 64}, {"type": "null"}]},
                "limit": {
                    "anyOf": [{"type": "integer", "minimum": 1, "maximum": 1000}, {"type": "null"}]
                },
            },
            ["query"],
        ),
        title="Cloud Logs Query",
        description="Query provider observability logs.",
    ),
    "cloud.metrics.query": _tool(
        cloud_metrics_query,
        read_only=True,
        requires_approval=False,
        schema=_schema(
            {
                **_PROVIDER,
                "query": {"type": "string", "minLength": 1, "maxLength": 8000},
                "service": {"anyOf": [{"type": "string", "maxLength": 64}, {"type": "null"}]},
                "window": {"anyOf": [{"type": "string", "maxLength": 64}, {"type": "null"}]},
                "limit": {
                    "anyOf": [{"type": "integer", "minimum": 1, "maximum": 1000}, {"type": "null"}]
                },
            },
            ["query"],
        ),
        title="Cloud Metrics Query",
        description="Query provider observability metrics.",
    ),
    "cloud.backup.create": _tool(
        cloud_backup,
        read_only=False,
        requires_approval=True,
        schema=_schema(
            {
                **_PROVIDER,
                "operation": {"type": "string", "const": "create"},
                "resource_id": {"anyOf": [{"type": "string", "maxLength": 256}, {"type": "null"}]},
                "options": {"anyOf": [{"type": "object"}, {"type": "null"}]},
            },
            ["operation"],
        ),
        title="Cloud Backup Create",
        description="Create a backup/snapshot via provider infrastructure APIs.",
    ),
    "cloud.costs.summary": _tool(
        cloud_costs_summary,
        read_only=True,
        requires_approval=False,
        schema=_schema(
            {
                **_PROVIDER,
                "period": {"anyOf": [{"type": "string", "maxLength": 64}, {"type": "null"}]},
                "group_by": {"anyOf": [{"type": "string", "maxLength": 64}, {"type": "null"}]},
            },
            [],
        ),
        title="Cloud Costs Summary",
        description="Get provider cost/usage summary data.",
    ),
    "cloud.ssh.exec": _tool(
        cloud_ssh_exec,
        read_only=False,
        requires_approval=True,
        schema=_schema(
            {
                "target": {"type": "string", "minLength": 1, "maxLength": 128},
                "command": {"type": "string", "minLength": 1, "maxLength": 20000},
                "timeout_seconds": {
                    "anyOf": [
                        {"type": "number", "minimum": 1, "maximum": 300},
                        {"type": "null"},
                    ]
                },
            },
            ["target", "command"],
        ),
        title="Cloud SSH Execute",
        description="Execute a command inside a VM over SSH (separate from cloud infrastructure APIs).",
        metadata={"trace_redact_result_fields": ["stdout", "stderr"]},
    ),
    "cloud.environment.create": _tool(
        cloud_environment_create,
        read_only=False,
        requires_approval=True,
        schema=_schema(
            {
                "branch": {"type": "string", "minLength": 1, "maxLength": 256},
                "commit_sha": {
                    "anyOf": [{"type": "string", "minLength": 7, "maxLength": 64}, {"type": "null"}]
                },
                "ttl_seconds": {
                    "anyOf": [
                        {"type": "integer", "minimum": 60, "maximum": 604800},
                        {"type": "null"},
                    ]
                },
            },
            ["branch"],
        ),
        title="Cloud Environment Create",
        description=(
            "Provision an isolated Alice sandbox environment on Cloud.ru (Container Apps "
            "Jobs or Compute VM) from a git commit snapshot, instead of local subprocess "
            "or SSH into a shared VM (confirmation required)."
        ),
    ),
    "cloud.environment.start": _tool(
        cloud_environment_start,
        read_only=False,
        requires_approval=True,
        schema=_schema(
            {"environment_id": {"type": "string", "minLength": 1, "maxLength": 64}},
            ["environment_id"],
        ),
        title="Cloud Environment Start",
        description="Start a Cloud.ru sandbox environment so it can run commands (confirmation required).",
    ),
    "cloud.environment.stop": _tool(
        cloud_environment_stop,
        read_only=False,
        requires_approval=True,
        schema=_schema(
            {"environment_id": {"type": "string", "minLength": 1, "maxLength": 64}},
            ["environment_id"],
        ),
        title="Cloud Environment Stop",
        description="Stop a Cloud.ru sandbox environment while keeping its resource allocated (confirmation required).",
    ),
    "cloud.environment.delete": _tool(
        cloud_environment_delete,
        read_only=False,
        requires_approval=True,
        schema=_schema(
            {"environment_id": {"type": "string", "minLength": 1, "maxLength": 64}},
            ["environment_id"],
        ),
        title="Cloud Environment Delete",
        description="Delete a Cloud.ru sandbox environment and its remote resource (confirmation required).",
    ),
    "cloud.environment.exec": _tool(
        cloud_environment_exec,
        read_only=False,
        requires_approval=True,
        schema=_schema(
            {
                "environment_id": {"type": "string", "minLength": 1, "maxLength": 64},
                "command": {"type": "string", "minLength": 1, "maxLength": 20000},
                "timeout_seconds": {
                    "anyOf": [
                        {"type": "number", "minimum": 1, "maximum": 300},
                        {"type": "null"},
                    ]
                },
            },
            ["environment_id", "command"],
        ),
        title="Cloud Environment Execute",
        description="Execute a command inside a running Cloud.ru sandbox environment (confirmation required).",
        metadata={"trace_redact_result_fields": ["stdout", "stderr"]},
    ),
}
