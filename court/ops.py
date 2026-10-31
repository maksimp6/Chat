"""The Court's actions over dialogs: traces, branches and the Yandex operations it may call.

Reading traces and listing operations changes nothing. A branch is a new dialog made from
the said messages of a dialog, with its own provider conversation: the original dialog is
never edited, so the accused never sees the Court in it. The Yandex operations are the
catalog's read and create operations (``yandex_client_modules.operations``); deletes,
cancels and updates are not offered to the Court.
"""

from __future__ import annotations

from typing import Any

from court.host import (
    bound,
    check_access,
    get_conversation_title,
    get_current_owner_id,
    get_invocation_status,
    get_invocation_trace,
    get_message_trace,
    list_message_traces,
)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _viewer() -> str | None:
    try:
        return get_current_owner_id(required=False)
    except bound.TreasuryIdentityError:
        return None


def _owner() -> str | None:
    try:
        return get_current_owner_id()
    except bound.TreasuryIdentityError:
        return None


def _int_list(value: Any, name: str) -> list[int]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(_is_int(item) for item in value):
        raise ValueError(f"{name} must be a list of integers")
    return value


def _edits(value: Any) -> dict[int, str]:
    if value is None:
        return {}
    if not isinstance(value, list):
        raise ValueError("edits must be a list of {message_id, text}")
    result: dict[int, str] = {}
    for item in value:
        if (
            not isinstance(item, dict)
            or not _is_int(item.get("message_id"))
            or not isinstance(item.get("text"), str)
        ):
            raise ValueError("each edit needs an integer message_id and a text")
        if item["message_id"] in result:
            raise ValueError(f"message {item['message_id']} is edited twice")
        result[item["message_id"]] = item["text"]
    return result


def trace_get(args: dict[str, Any], cfg=None) -> dict[str, Any]:
    """The trace of an invocation, or of the message that carries it, with the invocation's status."""
    invocation_id = str(args.get("invocation_id") or "").strip()
    if invocation_id:
        status = get_invocation_status(invocation_id)
        if status is None:
            return {"success": False, "error": "invocation_not_found"}
        return {
            "success": True,
            "invocation_id": invocation_id,
            "status": status.get("status"),
            "trace": get_invocation_trace(invocation_id),
        }
    conversation_id = str(args.get("conversation_id") or "")
    message_id = args.get("message_id")
    if not conversation_id or not _is_int(message_id):
        return {
            "success": False,
            "error": "trace_get needs invocation_id, or conversation_id and an integer message_id",
        }
    if not check_access(conversation_id, _viewer()):
        return {"success": False, "error": "conversation_not_found"}
    found = get_message_trace(conversation_id, message_id)
    if found is None:
        return {"success": False, "error": "message_not_found"}
    return {"success": True, **found}


def trace_refs(args: dict[str, Any], cfg=None) -> dict[str, Any]:
    """The messages of a dialog that carry a trace, with the invocation each one refers to."""
    conversation_id = str(args.get("conversation_id") or "")
    if not check_access(conversation_id, _viewer()):
        return {"success": False, "error": "conversation_not_found"}
    return {"success": True, "messages": list_message_traces(conversation_id)}


def branch(args: dict[str, Any], cfg=None) -> dict[str, Any]:
    """A new dialog from the said messages of ``conversation_id``, with edits and removed messages.

    ``before_message_id`` cuts the branch before that message; ``edits`` replaces the text of
    messages; ``drop_message_ids`` leaves messages out. The original dialog stays as it is."""
    conversation_id = str(args.get("conversation_id") or "")
    owner = _owner()
    if owner is None:
        return {"success": False, "error": "owner_unavailable"}
    before = args.get("before_message_id")
    if before is not None and not _is_int(before):
        return {"success": False, "error": "before_message_id must be an integer"}
    try:
        edits = _edits(args.get("edits"))
        drops = _int_list(args.get("drop_message_ids"), "drop_message_ids")
    except ValueError as error:
        return {"success": False, "error": str(error)}
    # A branch has no model parameter: it keeps the model of the dialog it copies.
    row = next(
        (r for r in bound.list_owned_conversations(owner) if r.get("id") == conversation_id),
        {},
    )
    model = row.get("model")
    title = str(args.get("title") or "").strip() or (
        "Ветка: " + (get_conversation_title(conversation_id, "") or conversation_id)
    )
    try:
        made = bound.fork_dialog(
            owner,
            conversation_id,
            model=model,
            title=title,
            before_id=before,
            edits=edits,
            drop_ids=drops,
        )
    except bound.ForkError as error:
        return {"success": False, "error": error.code, "status": error.status, **error.fields}
    return {"success": True, "branch": made}


def yandex_operations(args: dict[str, Any], cfg=None) -> dict[str, Any]:
    """The Yandex operations the Court may call: name, method, title and path parameters."""
    return {"success": True, "operations": bound.list_yandex_operations()}


def yandex_call(args: dict[str, Any], cfg=None) -> dict[str, Any]:
    """One Yandex API operation, validated against its contract, with the deployment's provider key."""
    operation = str(args.get("operation") or "").strip()
    path_params = args.get("path_params") or {}
    query = args.get("query") or {}
    if not isinstance(path_params, dict) or not isinstance(query, dict):
        return {"success": False, "error": "path_params and query must be objects"}
    try:
        answer = bound.call_yandex_operation(
            operation,
            path_params=path_params,
            query=query or None,
            body=args.get("body"),
        )
    except (bound.OperationRefused, bound.ContractViolation) as error:
        return {"success": False, "error": "operation_refused", "detail": str(error)}
    except bound.YandexClientError as error:
        return {"success": False, "error": "yandex_request_failed", "detail": str(error)}
    return {"success": True, "operation": operation, **answer}
