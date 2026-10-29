"""Reviewed outbound client contracts; refinements, not edits to the March snapshot.

These explicitly supported request shapes are narrower than the provider API.
Unknown fields/variants remain rejected. Sources were checked on 2026-10-24;
model-specific availability and response conformance still require separate checks.
"""

from __future__ import annotations

from typing import Any

AI = "https://aistudio.yandex.ru/en/docs/ai-studio/api/"
CLOUD = "https://yandex.cloud/en/docs/"
S = {"type": "string"}
TEXT = {"type": "string", "minLength": 1}
B = {"type": "boolean"}
I = {"type": "integer"}
SCHEMA_OBJECT = {"type": "object", "additionalProperties": True}


def obj(
    properties: dict[str, Any], *required: str, **constraints: Any
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
        **constraints,
    }


def array(items: dict[str, Any], **constraints: Any) -> dict[str, Any]:
    return {"type": "array", "items": items, **constraints}


def enum(*values: str) -> dict[str, Any]:
    return {"type": "string", "enum": list(values)}


def union(*schemas: dict[str, Any]) -> dict[str, Any]:
    return {"anyOf": list(schemas)}


def ref(name: str) -> dict[str, Any]:
    return {"$ref": "#/definitions/" + name}


def nullable(schema: dict[str, Any]) -> dict[str, Any]:
    return union(schema, {"type": "null"})


STRING_MAP = {"type": "object", "additionalProperties": S}
METADATA = {
    "type": "object",
    "maxProperties": 16,
    "propertyNames": {"maxLength": 64},
    "additionalProperties": {"type": "string", "maxLength": 512},
}
ATTRIBUTES = {
    "type": "object",
    "maxProperties": 16,
    "additionalProperties": union(
        {"type": "string", "maxLength": 512}, {"type": "number"}, B
    ),
}
STATUS = enum("in_progress", "completed", "incomplete")
CONTENT = union(
    obj({"type": enum("input_text", "output_text"), "text": S}, "type", "text"),
    obj(
        {
            "type": enum("input_image"),
            "image_url": S,
            "file_id": S,
            "detail": enum("auto", "low", "high"),
        },
        "type",
        anyOf=[{"required": ["image_url"]}, {"required": ["file_id"]}],
    ),
    obj(
        {
            "type": enum("input_file"),
            "file_data": S,
            "file_id": S,
            "filename": S,
            "file_url": S,
        },
        "type",
        anyOf=[
            {"required": ["file_data"]},
            {"required": ["file_id"]},
            {"required": ["file_url"]},
        ],
    ),
)
INPUT = union(
    obj(
        {
            "type": enum("message"),
            "role": enum("user", "system", "developer", "assistant"),
            "content": union(S, array(CONTENT)),
            "id": S,
            "status": STATUS,
        },
        "role",
        "content",
    ),
    obj(
        {
            "type": enum("function_call"),
            "call_id": TEXT,
            "name": TEXT,
            "arguments": S,
            "id": S,
            "status": STATUS,
        },
        "type",
        "call_id",
        "name",
        "arguments",
    ),
    obj(
        {
            "type": enum("function_call_output"),
            "call_id": TEXT,
            "output": union(S, array(CONTENT)),
            "id": S,
            "status": STATUS,
        },
        "type",
        "call_id",
        "output",
    ),
    obj(
        {
            "type": enum("reasoning"),
            "id": S,
            "encrypted_content": S,
            "summary": array(
                obj({"type": enum("summary_text"), "text": S}, "type", "text")
            ),
            "content": array(
                obj({"type": enum("reasoning_text"), "text": S}, "type", "text")
            ),
            "status": STATUS,
        },
        "type",
    ),
    obj(
        {
            "type": enum("mcp_approval_response"),
            "approval_request_id": TEXT,
            "approve": B,
            "id": S,
            "reason": S,
        },
        "type",
        "approval_request_id",
        "approve",
    ),
    obj({"type": enum("item_reference"), "id": TEXT}, "type", "id"),
)
FILTER = union(
    obj(
        {
            "type": enum("eq", "ne", "gt", "gte", "lt", "lte", "in", "nin"),
            "key": TEXT,
            "value": union(
                S, {"type": "number"}, B, array(union(S, {"type": "number"}))
            ),
        },
        "type",
        "key",
        "value",
    ),
    obj(
        {"type": enum("and", "or"), "filters": array(ref("Filter"))}, "type", "filters"
    ),
)
RANKING = obj(
    {
        "ranker": enum("auto", "none", "default-2024-11-15"),
        "score_threshold": {"type": "number", "minimum": 0, "maximum": 1},
        "hybrid_search": obj(
            {"embedding_weight": {"type": "number"}, "text_weight": {"type": "number"}}
        ),
    }
)
TOOLS = union(
    obj(
        {
            "type": enum("function"),
            "name": TEXT,
            "description": S,
            "parameters": nullable(SCHEMA_OBJECT),
            "strict": nullable(B),
            "defer_loading": B,
        },
        "type",
        "name",
    ),
    obj(
        {
            "type": enum("mcp"),
            "server_label": TEXT,
            "server_url": TEXT,
            "connector_id": TEXT,
            "authorization": S,
            "headers": STRING_MAP,
            "server_description": S,
            "defer_loading": B,
            "allowed_tools": union(
                array(S), obj({"tool_names": array(S), "read_only": B})
            ),
            "require_approval": union(
                enum("always", "never"),
                obj(
                    {
                        "always": obj({"tool_names": array(S)}, "tool_names"),
                        "never": obj({"tool_names": array(S)}, "tool_names"),
                    }
                ),
            ),
        },
        "type",
        "server_label",
        anyOf=[{"required": ["server_url"]}, {"required": ["connector_id"]}],
    ),
    obj(
        {
            "type": enum("file_search"),
            "vector_store_ids": array(TEXT),
            "max_num_results": {"type": "integer", "minimum": 1, "maximum": 50},
            "ranking_options": RANKING,
            "filters": ref("Filter"),
        },
        "type",
        "vector_store_ids",
    ),
    obj(
        {
            "type": enum("web_search", "web_search_preview"),
            "search_context_size": enum("low", "medium", "high"),
            "filters": obj({"allowed_domains": array(S)}),
            "user_location": obj(
                {
                    "type": enum("approximate"),
                    "city": S,
                    "country": S,
                    "region": S,
                    "timezone": S,
                }
            ),
        },
        "type",
    ),
)
FORMAT = union(
    obj({"type": enum("text", "json_object")}, "type"),
    obj(
        {
            "type": enum("json_schema"),
            "name": TEXT,
            "schema": SCHEMA_OBJECT,
            "description": S,
            "strict": nullable(B),
        },
        "type",
        "name",
        "schema",
    ),
)
RESPONSES = obj(
    {
        "model": TEXT,
        "input": union(S, array(INPUT)),
        "instructions": S,
        "metadata": METADATA,
        "background": B,
        "store": B,
        "stream": B,
        "previous_response_id": nullable(TEXT),
        "conversation": nullable(union(TEXT, obj({"id": TEXT}, "id"))),
        "temperature": {"type": "number", "minimum": 0, "maximum": 2},
        "top_p": {"type": "number", "minimum": 0, "maximum": 1},
        "max_output_tokens": {"type": "integer", "minimum": 16},
        "max_tool_calls": {"type": "integer", "minimum": 0},
        "top_logprobs": {"type": "integer", "minimum": 0, "maximum": 20},
        "prompt_cache_key": S,
        "service_tier": enum("auto", "default", "flex", "scale", "priority"),
        "reasoning": obj(
            {"effort": enum("none", "minimal", "low", "medium", "high", "xhigh")}
        ),
        "text": obj({"format": FORMAT, "verbosity": enum("low", "medium", "high")}),
        "tools": array(TOOLS),
        "parallel_tool_calls": B,
        "tool_choice": union(
            enum("auto", "none", "required"),
            obj({"type": enum("function"), "name": TEXT}, "type", "name"),
            obj(
                {"type": enum("mcp"), "server_label": TEXT, "name": S},
                "type",
                "server_label",
            ),
            obj(
                {"type": enum("file_search", "web_search", "web_search_preview")},
                "type",
            ),
        ),
        "prompt": obj({"id": TEXT, "version": S, "variables": STRING_MAP}, "id"),
        "truncation": enum("auto", "disabled"),
    },
    "model",
    "input",
)
RESPONSES["not"] = {
    "required": ["conversation", "previous_response_id"],
    "properties": {
        "conversation": {"not": {"type": "null"}},
        "previous_response_id": {"not": {"type": "null"}},
    },
}

PROFILES: dict[str, dict[str, Any]] = {}


def add(
    key: str,
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
    query: dict[str, Any] | None = None,
    host: str = "ai.api.cloud.yandex.net",
    source: str,
    body_format: str = "json",
) -> None:
    import re

    locations = {}
    definitions = {"Filter": FILTER}
    for location, shape in (("body", body), ("query", query)):
        if shape is not None:
            definitions[location] = shape
            locations[location] = "#/definitions/" + location
    path_names = re.findall(r"\{([^}]+)\}", path)
    if path_names:
        definitions["path"] = obj({name: TEXT for name in path_names}, *path_names)
        locations["path"] = "#/definitions/path"
    name = "yandex.runtime." + key
    PROFILES[name] = {
        "name": name,
        "availability": "not_verified_live",
        "profile_for": key,
        "transport": {
            "kind": "http",
            "method": method,
            "url_template": "https://" + host + path,
        },
        "request": locations,
        "responses": {},
        "body_format": body_format,
        "schema": {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "definitions": definitions,
        },
        "source": {
            "url": source,
            "checked_on": "2026-10-24",
            "scope": "reviewed outbound client subset; not complete provider API",
        },
    }


add(
    "responses.create",
    "POST",
    "/v1/responses",
    body=RESPONSES,
    source=AI + "Responses/createResponse",
)
add(
    "responses.get",
    "GET",
    "/v1/responses/{response_id}",
    query=obj({"stream": B, "starting_after": I}),
    source=AI + "Responses/getResponse",
)
add(
    "conversations.create",
    "POST",
    "/v1/conversations",
    body=obj({"metadata": METADATA, "items": array(INPUT)}),
    source=AI + "Conversations/createConversation",
)
LIMIT = {"type": "integer", "minimum": 1, "maximum": 100}
EXPIRES = obj(
    {
        "anchor": enum("last_active_at"),
        "days": {"type": "integer", "minimum": 1, "maximum": 365},
    },
    "anchor",
    "days",
)
CHUNKING = union(
    obj({"type": enum("auto")}, "type"),
    obj(
        {
            "type": enum("static"),
            "static": obj(
                {
                    "max_chunk_size_tokens": {
                        "type": "integer",
                        "minimum": 100,
                        "maximum": 4096,
                    },
                    "chunk_overlap_tokens": {"type": "integer", "minimum": 0},
                },
                "max_chunk_size_tokens",
                "chunk_overlap_tokens",
            ),
        },
        "type",
        "static",
    ),
)
add(
    "vector.create",
    "POST",
    "/v1/vector_stores",
    body=obj(
        {
            "name": S,
            "description": S,
            "file_ids": array(TEXT),
            "chunking_strategy": CHUNKING,
            "expires_after": EXPIRES,
            "metadata": METADATA,
        }
    ),
    source=AI + "Vector-stores/createVectorStore",
)
add(
    "vector.list",
    "GET",
    "/v1/vector_stores",
    query=obj({"limit": LIMIT, "order": enum("asc", "desc"), "after": S, "before": S}),
    source=AI + "Vector-stores/listVectorStores",
)
for verb, operation in (("GET", "get"), ("DELETE", "delete")):
    add(
        "vector." + operation,
        verb,
        "/v1/vector_stores/{vector_store_id}",
        source=AI + "Vector-stores/" + operation + "VectorStore",
    )
add(
    "vector.files.list",
    "GET",
    "/v1/vector_stores/{vector_store_id}/files",
    query=obj(
        {
            "limit": LIMIT,
            "order": enum("asc", "desc"),
            "after": S,
            "before": S,
            "filter": enum("in_progress", "completed", "failed", "cancelled"),
        }
    ),
    source=AI + "Vector-stores/listVectorStoreFiles",
)
add(
    "vector.files.create",
    "POST",
    "/v1/vector_stores/{vector_store_id}/files",
    body=obj(
        {
            "file_id": TEXT,
            "chunking_strategy": CHUNKING,
            "attributes": nullable(ATTRIBUTES),
        },
        "file_id",
    ),
    source=AI + "Vector-stores/createVectorStoreFile",
)
for verb, operation in (("GET", "get"), ("DELETE", "delete")):
    add(
        "vector.files." + operation,
        verb,
        "/v1/vector_stores/{vector_store_id}/files/{file_id}",
        source=AI + "Vector-stores/" + operation + "VectorStoreFile",
    )
add(
    "vector.search",
    "POST",
    "/v1/vector_stores/{vector_store_id}/search",
    body=obj(
        {
            "query": union(TEXT, array(TEXT)),
            "rewrite_query": B,
            "max_num_results": {"type": "integer", "minimum": 1, "maximum": 50},
            "filters": FILTER,
            "ranking_options": RANKING,
        },
        "query",
    ),
    source=AI + "Vector-stores/searchVectorStore",
)
add(
    "files.list",
    "GET",
    "/v1/files",
    query=obj(
        {
            "limit": {"type": "integer", "minimum": 1, "maximum": 10000},
            "purpose": S,
            "order": enum("asc", "desc"),
            "after": S,
        }
    ),
    source=AI + "Files/listFiles",
)
for verb, operation in (("GET", "get"), ("DELETE", "delete")):
    add(
        "files." + operation,
        verb,
        "/v1/files/{file_id}",
        source=AI + "Files/" + operation + "File",
    )
add(
    "files.content",
    "GET",
    "/v1/files/{file_id}/content",
    source=AI + "Files/downloadFile",
)
add(
    "files.upload",
    "POST",
    "/v1/files",
    body=obj(
        {
            "purpose": enum(
                "assistants", "batch", "fine-tune", "vision", "user_data", "evals"
            ),
            "expires_after": obj(
                {
                    "anchor": enum("created_at"),
                    "seconds": {"type": "integer", "minimum": 3600, "maximum": 2592000},
                },
                "anchor",
                "seconds",
            ),
        },
        "purpose",
    ),
    body_format="multipart",
    source=AI + "Files/createFile",
)
add(
    "completion.create",
    "POST",
    "/foundationModels/v1/completion",
    host="llm.api.cloud.yandex.net",
    body=obj(
        {
            "modelUri": TEXT,
            "completionOptions": obj(
                {
                    "stream": B,
                    "temperature": {"type": "number", "minimum": 0, "maximum": 1},
                    "maxTokens": {
                        "type": "string",
                        "format": "int64",
                        "pattern": "^[1-9][0-9]*$",
                    },
                    "reasoningOptions": obj(
                        {
                            "mode": enum(
                                "REASONING_MODE_UNSPECIFIED",
                                "DISABLED",
                                "ENABLED_HIDDEN",
                            )
                        }
                    ),
                }
            ),
            "messages": array(
                obj(
                    {"role": enum("system", "user", "assistant"), "text": S},
                    "role",
                    "text",
                )
            ),
            "jsonObject": B,
            "jsonSchema": obj({"schema": SCHEMA_OBJECT}, "schema"),
        },
        "modelUri",
        "messages",
    ),
    source="https://aistudio.yandex.ru/en/docs/ai-studio/text-generation/api-ref/TextGeneration/completion",
)
PROFILES["yandex.runtime.completion.create"]["schema"]["definitions"]["body"]["not"] = {
    "required": ["jsonObject", "jsonSchema"]
}
add(
    "iam.create",
    "POST",
    "/iam/v1/apiKeys",
    host="iam.api.cloud.yandex.net",
    body=obj(
        {
            "serviceAccountId": {"type": "string", "maxLength": 50},
            "description": {"type": "string", "maxLength": 256},
            "scopes": array({"type": "string", "maxLength": 256}, maxItems=100),
            "expiresAt": {"type": "string", "format": "date-time"},
        }
    ),
    source=CLOUD + "iam/api-ref/ApiKey/create",
)
add(
    "iam.delete",
    "DELETE",
    "/iam/v1/apiKeys/{api_key_id}",
    host="iam.api.cloud.yandex.net",
    source=CLOUD + "iam/api-ref/ApiKey/delete",
)
add(
    "speech.stt",
    "POST",
    "/speech/v1/stt:recognize",
    host="stt.api.cloud.yandex.net",
    query=obj(
        {
            "topic": S,
            "lang": S,
            "format": enum("lpcm", "oggopus"),
            "sampleRateHertz": enum("8000", "16000", "48000"),
            "folderId": {"type": "string", "maxLength": 50},
            "profanityFilter": B,
            "rawResults": B,
        }
    ),
    body_format="audio",
    source="https://aistudio.yandex.ru/en/docs/speechkit/stt/api/request-api",
)
add(
    "speech.tts",
    "POST",
    "/speech/v1/tts:synthesize",
    host="tts.api.cloud.yandex.net",
    body=obj(
        {
            "text": {"type": "string", "maxLength": 5000},
            "lang": enum("ru-RU"),
            "voice": S,
            "format": enum("oggopus", "lpcm", "mp3"),
            "folderId": {"type": "string", "maxLength": 50},
            "sampleRateHertz": enum("8000", "16000", "48000"),
            "speed": {
                "type": "string",
                "pattern": "^(?:0\\.[1-9][0-9]*|[12](?:\\.[0-9]+)?|3(?:\\.0+)?)$",
            },
        },
        "text",
    ),
    body_format="form",
    source="https://aistudio.yandex.ru/en/docs/speechkit/tts/request",
)
