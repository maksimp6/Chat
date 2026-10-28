"""Judge a fresh Logos tools reply from its invocation evidence, without a model call."""

import hashlib
import json
import math
from pathlib import Path
import re
import stat
import zipfile


SELF_CHECK_QUESTION = (
    "Логос, сначала выполни настоящие вызовы инструментов, затем дай краткий итог. "
    "Не заменяй function_call текстом о выполнении. Читай только собственные тестовые данные; "
    "записывай только в указанную новую временную папку. Секреты, личные данные, устройства, "
    "платные дополнительные сервисы и внешние публикации не затрагивай. Не меняй доступ, "
    "настройки или ограничения; требуемое разрешение обозначь BLOCKED. Выполняй только "
    "перечисленный ниже безопасный пакет; все остальные инструменты обозначь "
    "NOT_RUN с короткой причиной. Каталог и твои слова не доказывают вызов или отсутствие "
    "лимитов. Итог до 12 строк: фактические вызовы, SHA256, ошибки и NOT_RUN; неизвестное UNKNOWN."
)

_NAME = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")
_SECRET = re.compile(r"gh[pousr]_|github_pat_|sk-|AQVN|BEGIN", re.IGNORECASE)
_PHASES = {"execution", "validation", "authorization", "approval_required", "transport"}
_BLOCKED = {"authorization", "approval_required", "transport"}
_BOOL_SETTINGS = {"background", "store", "stream", "parallel_tool_calls"}
_NUMBER_SETTINGS = {"temperature", "top_p", "max_output_tokens", "max_tool_calls"}
_ENUM_SETTINGS = {
    "truncation": {"auto", "disabled"},
    "tool_choice": {"auto", "none", "required"},
    "service_tier": {"auto", "default", "flex", "priority"},
}
_EFFORT = {"none", "minimal", "low", "medium", "high", "xhigh", "max"}
_TOOL_TYPES = {
    "function",
    "mcp",
    "file_search",
    "web_search",
    "code_interpreter",
    "other",
}
_RESPONSE_STATUSES = {
    "completed",
    "incomplete",
    "failed",
    "cancelled",
    "queued",
    "in_progress",
}
_INCOMPLETE_REASONS = {"max_output_tokens", "content_filter"}
SAFE_SELF_CHECK_TOOLS = frozenset(
    {
        "write_file",
        "read_file",
        "calculate_hash",
        "zip_compress",
        "make_directory",
        "apply_patch",
        "copy_or_move_file",
        "delete_path",
        "zip_extract",
        "list_directory",
        "glob_search",
        "grep_search",
        "file_stat",
        "python_ast_outline",
        "get_system_info",
        "local_runtime_exec",
        "git_status",
        "git_log",
        "git_diff",
        "git_branches",
    }
)


def _name(value):
    return (
        isinstance(value, str)
        and bool(_NAME.fullmatch(value))
        and not _SECRET.search(value)
    )


def _digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _fixture_paths(nonce, folder):
    if not isinstance(nonce, str) or not re.fullmatch(r"[0-9a-f]{32}", nonce):
        raise ValueError("Use a fresh UUID hex nonce")
    if folder != "tmp/logos-tools-check-" + nonce:
        raise ValueError("Use the nonce-bound relative temporary folder")
    return (
        folder + "/payload.txt",
        folder + "/payload.zip",
        "LOGOS_TOOLS_SELF_CHECK:" + nonce,
    )


def build_self_check_question(inventory, nonce, folder, project_root=None):
    """Keep the task small enough to call tools before generating a long catalogue review."""
    text_path, archive_path, content = _fixture_paths(nonce, folder)
    root = Path(project_root) if project_root is not None else None
    if root is not None and (
        not root.is_absolute() or any(part in {".", ".."} for part in root.parts)
    ):
        raise ValueError("Use the absolute isolated candidate repository root")
    scratch_name = "scratch-" + nonce + ".txt"
    scratch = folder + "/" + scratch_name
    duplicate = folder + "/delete-me.txt"
    fixture = folder + "/fixture.py"
    calls = [
        {"name": "write_file", "arguments": {"path": text_path, "content": content}},
        {
            "name": "read_file",
            "arguments": {
                "path": text_path,
                "offset": 0,
                "length": len(content),
                "whence": "start",
            },
        },
        {
            "name": "calculate_hash",
            "arguments": {"path": text_path, "algorithm": "sha256"},
        },
        {
            "name": "zip_compress",
            "arguments": {"source_path": text_path, "archive_path": archive_path},
        },
    ]
    if root is not None:
        calls.extend(
            [
                {"name": "make_directory", "arguments": {"path": folder + "/empty"}},
                {
                    "name": "write_file",
                    "arguments": {"path": scratch, "content": "before:" + nonce},
                },
                {
                    "name": "apply_patch",
                    "arguments": {
                        "path": scratch,
                        "search_text": "before:",
                        "replace_text": "after:",
                    },
                },
                {
                    "name": "copy_or_move_file",
                    "arguments": {"src": scratch, "dst": duplicate, "action": "copy"},
                },
                {"name": "delete_path", "arguments": {"path": duplicate}},
                {
                    "name": "zip_extract",
                    "arguments": {
                        "archive_path": archive_path,
                        "target_dir": folder + "/extracted",
                    },
                },
                {"name": "list_directory", "arguments": {"path": folder}},
                {"name": "glob_search", "arguments": {"pattern": folder + "/*"}},
                {
                    "name": "grep_search",
                    "arguments": {
                        "query": nonce,
                        "file_pattern": scratch_name,
                        "max_matches": 5,
                    },
                },
                {"name": "file_stat", "arguments": {"path": text_path}},
                {
                    "name": "write_file",
                    "arguments": {"path": fixture, "content": "answer = 42\n"},
                },
                {"name": "python_ast_outline", "arguments": {"path": fixture}},
                {"name": "get_system_info", "arguments": {}},
                {
                    "name": "local_runtime_exec",
                    "arguments": {
                        "command": "printf '%s' " + nonce,
                        "cwd": str(root / folder),
                        "timeout_seconds": 5,
                    },
                },
                {"name": "git_status", "arguments": {"repo_path": str(root)}},
                {"name": "git_log", "arguments": {"repo_path": str(root), "limit": 1}},
                {
                    "name": "git_diff",
                    "arguments": {"repo_path": str(root), "file_path": scratch},
                },
                {"name": "git_branches", "arguments": {"repo_path": str(root)}},
            ]
        )
    categories = inventory.get("categories") if isinstance(inventory, dict) else None
    active = inventory.get("active_categories") if isinstance(inventory, dict) else None
    if (
        not isinstance(categories, dict)
        or not isinstance(active, list)
        or any(
            not _name(category)
            or not isinstance(names, list)
            or any(not _name(name) for name in names)
            for category, names in categories.items()
        )
        or any(
            not isinstance(category, str) or category not in categories
            for category in active
        )
    ):
        raise ValueError("Tool inventory is unavailable or invalid")
    names = {name for category in active for name in categories[category]}
    package = (
        "20 различных инструментов, 22 вызова с учётом тестовых файлов. "
        "Git читает только явно указанный кандидат. "
        if root is not None
        else "4 инструмента, 4 вызова. "
    )
    return (
        SELF_CHECK_QUESTION
        + " Пакет: "
        + package
        + "Выполни последовательно: "
        + json.dumps(calls, ensure_ascii=False, separators=(",", ":"))
        + " Удалять можно только delete-me.txt; payload.txt и payload.zip сохрани для независимой проверки. "
        + "В активном каталоге "
        + str(len(names))
        + " инструментов. Остальные NOT_RUN: вне безопасного пакета."
    )


def verify_file_effects(project_root, nonce, folder, extended=False):
    """Independently inspect only the nonce-bound test files, never execute a tool."""
    text_path, archive_path, content = _fixture_paths(nonce, folder)
    root = Path(project_root).resolve()
    expected = content.encode("utf-8")
    report = {
        "status": "FAIL",
        "scope": "nonce_bound_text_and_zip_effects_only",
        "expected_sha256": hashlib.sha256(expected).hexdigest(),
        "checks": {},
    }
    try:

        def guarded_path(relative):
            path = root / relative
            for part in [path, *path.parents]:
                if part == root:
                    break
                if part.is_symlink():
                    raise ValueError("Test path is a symlink")
            return path

        def read_regular(path):
            size = path.stat()
            if not stat.S_ISREG(size.st_mode) or size.st_size > 1024 * 1024:
                raise ValueError("Test artifact is not a bounded regular file")
            return path.read_bytes()

        paths = [guarded_path(text_path), guarded_path(archive_path)]
        payload = read_regular(paths[0])
        # Check the archive's file type and bound before ZipFile opens it.
        read_regular(paths[1])
        report["checks"]["text_matches_nonce"] = payload == expected
        report["actual_sha256"] = hashlib.sha256(payload).hexdigest()
        with zipfile.ZipFile(paths[1]) as archive:
            entries = archive.infolist()
            valid = (
                len(entries) == 1
                and entries[0].filename == "payload.txt"
                and entries[0].file_size == len(expected)
            )
            report["checks"]["zip_contains_exact_nonce_file"] = (
                valid and archive.read(entries[0]) == expected
            )
        if extended:
            report["scope"] = "nonce_bound_extended_test_file_effects_only"
            report["checks"]["scratch_patch_matches_nonce"] = read_regular(
                guarded_path(folder + "/scratch-" + nonce + ".txt")
            ) == ("after:" + nonce).encode("utf-8")
            deleted = guarded_path(folder + "/delete-me.txt")
            report["checks"]["temporary_copy_deleted"] = not deleted.exists()
            report["checks"]["zip_extracted_exact_nonce_file"] = (
                read_regular(guarded_path(folder + "/extracted/payload.txt"))
                == expected
            )
            report["checks"]["temporary_directory_created"] = guarded_path(
                folder + "/empty"
            ).is_dir()
            report["checks"]["ast_fixture_matches"] = (
                read_regular(guarded_path(folder + "/fixture.py")) == b"answer = 42\n"
            )
        if all(report["checks"].values()):
            report["status"] = "PASS"
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile):
        report["reason"] = "test_files_missing_invalid_or_unreadable"
    return report


def _signature(message):
    return tuple(message.get(key) for key in ("role", "text", "invocation_id"))


def _bind_reply(before, after, question):
    if not isinstance(question, str) or not question.strip():
        return [], "question_missing"
    if not isinstance(before, list) or not isinstance(after, list):
        return [], "message_history_unavailable"
    if not all(isinstance(message, dict) for message in before + after):
        return [], "message_history_invalid"
    if len(after) <= len(before) or [_signature(m) for m in after[: len(before)]] != [
        _signature(m) for m in before
    ]:
        return [], "message_history_not_append_only"
    fresh = after[len(before) :]
    if fresh[0].get("role") != "user" or fresh[0].get("text") != question:
        return [], "exact_new_question_not_persisted"
    if any(message.get("role") == "user" for message in fresh[1:]):
        return [], "reply_binding_ambiguous"
    replies = [m for m in fresh[1:] if m.get("role") == "assistant"]
    if not replies or any(
        not isinstance(m.get("text"), str) or not m["text"].strip() for m in replies
    ):
        return [], "fresh_nonempty_reply_missing"
    previous_ids = {
        m.get("invocation_id")
        for m in before
        if isinstance(m.get("invocation_id"), str)
    }
    ids = []
    for message in replies:
        invocation_id = message.get("invocation_id")
        if (
            not isinstance(invocation_id, str)
            or not invocation_id
            or invocation_id in previous_ids
        ):
            return [], "fresh_invocation_missing_or_reused"
        if invocation_id not in ids:
            ids.append(invocation_id)
    return ids, None


def _request_evidence(trace):
    """Allowlisted structural/scalar settings only; never copy arbitrary payload data."""
    result = []
    events = trace.get("events")
    for event in events if isinstance(events, list) else []:
        if not isinstance(event, dict) or event.get("type") != "api_request_manifest":
            continue
        payload = event.get("payload")
        manifest = payload.get("manifest") if isinstance(payload, dict) else None
        if (
            not isinstance(manifest, dict)
            or manifest.get("capture_stage") != "assembled_payload_before_post"
        ):
            continue
        request = {
            "capture_stage": "assembled_payload_before_post",
            "provider_effective": "UNKNOWN",
            "settings": {},
        }
        settings = manifest.get("settings")
        for key in (
            _BOOL_SETTINGS | _NUMBER_SETTINGS | set(_ENUM_SETTINGS) | {"reasoning"}
        ):
            field = settings.get(key) if isinstance(settings, dict) else None
            if (
                not isinstance(field, dict)
                or not isinstance(field.get("state"), str)
                or field["state"] not in {"present", "absent"}
            ):
                continue
            safe = {"state": field["state"]}
            value = field.get("value")
            if field["state"] == "present":
                if key in _BOOL_SETTINGS and type(value) is bool:
                    safe["value"] = value
                elif key in _NUMBER_SETTINGS and (
                    type(value) is int or type(value) is float and math.isfinite(value)
                ):
                    safe["value"] = value
                elif (
                    key in _ENUM_SETTINGS
                    and isinstance(value, str)
                    and value in _ENUM_SETTINGS[key]
                ):
                    safe["value"] = value
                elif (
                    key == "reasoning"
                    and isinstance(field.get("effort"), str)
                    and field["effort"] in _EFFORT
                ):
                    safe["effort"] = field["effort"]
            request["settings"][key] = safe
        tools = manifest.get("tools")
        if isinstance(tools, dict):
            if type(tools.get("count")) is int and tools["count"] >= 0:
                request["offered_tool_count"] = tools["count"]
            types = tools.get("types")
            if isinstance(types, dict):
                request["offered_tool_types"] = {
                    key: value
                    for key, value in types.items()
                    if key in _TOOL_TYPES and type(value) is int and value >= 0
                }
        result.append(request)
    return result


def _provider_evidence(trace):
    """Read provider-authored call identities; executor records alone are insufficient."""
    calls, responses = {}, []
    for entry in (
        trace.get("responses", []) if isinstance(trace.get("responses"), list) else []
    ):
        raw = entry.get("raw") if isinstance(entry, dict) else None
        if not isinstance(raw, dict):
            continue
        status = raw.get("status")
        response = {
            "status": status
            if isinstance(status, str) and status in _RESPONSE_STATUSES
            else "UNKNOWN",
            "error_present": raw.get("error") is not None,
        }
        response_id = raw.get("id") or entry.get("response_id")
        if isinstance(response_id, str) and response_id:
            response["response_id_sha256"] = _digest(response_id)
        details = raw.get("incomplete_details")
        reason = details.get("reason") if isinstance(details, dict) else None
        if status == "incomplete":
            response["incomplete_reason"] = (
                reason
                if isinstance(reason, str) and reason in _INCOMPLETE_REASONS
                else "UNKNOWN"
            )
        responses.append(response)
        output = raw.get("output", []) if isinstance(raw.get("output"), list) else []
        candidates = []
        for item in output:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "message" and item.get("status") in (
                None,
                "completed",
            ):
                candidates.extend(
                    item.get("content", [])
                    if isinstance(item.get("content"), list)
                    else []
                )
            else:
                candidates.append(item)
        for item in candidates:
            if not isinstance(item, dict) or item.get("type") != "function_call":
                continue
            if item.get("status") not in (None, "completed"):
                continue
            name, call_id = item.get("name"), item.get("call_id")
            if _name(name) and isinstance(call_id, str) and call_id:
                step = entry.get("step")
                if step is None or type(step) is int:
                    calls.setdefault(call_id, set()).add((name, step))
    return calls, responses


def _offered_evidence(trace):
    """Inspect the captured outgoing payload, rather than treating a catalogue as sent."""
    receipts = []
    for event in (
        trace.get("events", []) if isinstance(trace.get("events"), list) else []
    ):
        if not isinstance(event, dict) or event.get("type") != "api_request_manifest":
            continue
        payload = event.get("payload")
        manifest = payload.get("manifest") if isinstance(payload, dict) else None
        if (
            not isinstance(manifest, dict)
            or manifest.get("capture_stage") != "assembled_payload_before_post"
        ):
            continue
        tools = manifest.get("tools")
        if (
            not isinstance(tools, dict)
            or tools.get("function_names_complete") is not True
        ):
            continue
        batches, count, types = (
            tools.get("function_name_batches"),
            tools.get("function_name_count"),
            tools.get("types"),
        )
        if (
            not isinstance(batches, list)
            or type(count) is not int
            or count < 0
            or not isinstance(types, dict)
            or type(types.get("function")) is not int
            or types["function"] != count
            or any(not isinstance(batch, list) or len(batch) > 40 for batch in batches)
        ):
            continue
        names = [name for batch in batches for name in batch]
        if (
            len(names) != count
            or any(not _name(name) for name in names)
            or len(set(names)) != count
        ):
            continue
        receipts.append(
            {
                "names": set(names),
                "complete": True,
                "source": "assembled_request_manifest_function_names",
            }
        )
    # api_requests.payload is independently sanitized and may have lost every
    # item after its first 50. Complete pre-sanitization receipts take precedence.
    if receipts:
        return receipts
    inventories = []
    for entry in (
        trace.get("api_requests", [])
        if isinstance(trace.get("api_requests"), list)
        else []
    ):
        payload = entry.get("payload") if isinstance(entry, dict) else None
        tools = payload.get("tools") if isinstance(payload, dict) else None
        if not isinstance(tools, list):
            continue
        names = set()
        malformed = False
        for tool in tools:
            if not isinstance(tool, dict):
                malformed = True
                continue
            kind = tool.get("type")
            if not isinstance(kind, str) or kind not in _TOOL_TYPES or kind == "other":
                malformed = True
                continue
            if kind != "function":
                continue
            function = tool.get("function")
            name = tool.get("name") or (
                function.get("name") if isinstance(function, dict) else None
            )
            if _name(name):
                names.add(name)
            else:
                malformed = True
        inventories.append(
            {
                "names": names,
                "complete": not malformed,
                "source": "captured_api_request_payload",
            }
        )
    return inventories


def _call_evidence(call, provider_calls):
    result = call.get("result")
    result = result if isinstance(result, dict) else {}
    metadata = result.get("metadata")
    raw_phase = metadata.get("phase") if isinstance(metadata, dict) else None
    phase = (
        raw_phase if isinstance(raw_phase, str) and raw_phase in _PHASES else "UNKNOWN"
    )
    if phase in _BLOCKED:
        status = "BLOCKED"
    elif call.get("error") or result.get("error") or result.get("success") is False:
        status = "FAIL"
    # The real executor supplies executor/duration metadata on success, but
    # does not add a phase. Its explicit success flag still proves the call
    # envelope completed; it does not prove the effect or unrestricted inputs.
    elif result.get("success") is True and (phase == "execution" or raw_phase is None):
        status = "CALL_SUCCEEDED"
    else:
        status = "UNKNOWN"
    evidence = {"status": status, "phase": phase, "model_linked": False}
    call_id = call.get("call_id")
    if isinstance(call_id, str) and call_id:
        evidence["call_id_sha256"] = _digest(call_id)
        matches = provider_calls.get(call_id, set())
        name = call.get("name", call.get("tool_name"))
        step = call.get("step")
        # A reused identity, a different tool name, or a different request round
        # cannot establish that this executor entry came from this model call.
        if len(matches) == 1:
            provider_name, provider_step = next(iter(matches))
            evidence["model_linked"] = provider_name == name and (
                step is None or provider_step is None or step == provider_step
            )
        if not evidence["model_linked"]:
            evidence.update(
                status="UNKNOWN",
                reason="provider_function_call_link_missing_or_ambiguous",
            )
    else:
        evidence["status"] = "UNKNOWN"
        evidence["reason"] = "model_call_link_missing"
    return evidence


def evaluate_tool_evidence(
    categories, active_categories, before_messages, after_messages, traces, question
):
    """Return conservative coverage; prose never establishes execution or unbounded limits.

    ``traces`` maps fresh assistant invocation IDs to the corresponding trace JSON.
    A provider function_call must match the executor name and call_id. A successful
    envelope establishes one completed call only, not its real effect, every
    argument combination, or unlimited provider/adapter capabilities.
    """
    report = {
        "status": "NOT_RUN",
        "tools": [],
        "inactive_tools": [],
        "coverage": {
            "status": "NOT_RUN",
            "expected": 0,
            "called": 0,
            "succeeded": 0,
            "catalogue_count": 0,
            "offered_count": None,
            "scope": "active_catalogue_model_linked_call_envelopes_only; effects_not_independently_verified",
        },
        "limits": {
            "status": "UNKNOWN",
            "unbounded": "UNKNOWN",
            "parameter_domains": "UNKNOWN",
            "provider_effective": "UNKNOWN",
            "requests": [],
            "reason": "finite_calls_do_not_prove_unbounded_parameters",
        },
        "hosted_and_mcp": {
            "status": "UNKNOWN",
            "reason": "registry_categories_do_not_prove_hosted_or_remote_inventory",
        },
        "inventory": {
            "status": "UNKNOWN",
            "offered": [],
            "not_offered": [],
            "offered_outside_catalogue": [],
        },
        "provider": {"status": "UNKNOWN", "responses": []},
    }
    if (
        not isinstance(categories, dict)
        or not categories
        or not isinstance(active_categories, list)
        or any(
            not _name(category)
            or not isinstance(names, list)
            or any(not _name(name) for name in names)
            for category, names in categories.items()
        )
        or any(
            not isinstance(category, str) or category not in categories
            for category in active_categories
        )
    ):
        report.update(status="ERROR", reason="tool_inventory_invalid_or_unavailable")
        return report
    active = set(active_categories)
    expected = {
        name
        for category, names in categories.items()
        if category in active
        for name in names
    }
    inactive = {
        name
        for category, names in categories.items()
        if category not in active
        for name in names
    } - expected
    report["inactive_tools"] = sorted(inactive)
    report["coverage"]["expected"] = len(expected)
    report["coverage"]["catalogue_count"] = len(expected)
    report["tools"] = [
        {"name": name, "status": "NOT_RUN", "calls": []} for name in sorted(expected)
    ]
    ids, reason = _bind_reply(before_messages, after_messages, question)
    if reason:
        report["reason"] = reason
        return report
    if not expected:
        report["reason"] = "no_active_tools"
        return report
    report["binding"] = {
        "status": "BOUND",
        "question_sha256": _digest(question),
        "invocation_id_sha256": [_digest(value) for value in ids],
    }
    calls = {name: [] for name in expected}
    trace_failures = 0
    missing_traces = 0
    unexpected_calls = 0
    unlinked_calls = 0
    inventories = []
    for invocation_id in ids:
        trace = traces.get(invocation_id) if isinstance(traces, dict) else None
        context = trace.get("context") if isinstance(trace, dict) else None
        if (
            not isinstance(context, dict)
            or context.get("invocation_id") != invocation_id
        ):
            missing_traces += 1
            continue
        errors = trace.get("errors")
        trace_failures += len(errors) if isinstance(errors, list) else 0
        report["limits"]["requests"].extend(_request_evidence(trace))
        provider_calls, provider_responses = _provider_evidence(trace)
        report["provider"]["responses"].extend(provider_responses)
        inventories.extend(_offered_evidence(trace))
        entries = trace.get("tool_calls")
        for call in entries if isinstance(entries, list) else []:
            if not isinstance(call, dict):
                continue
            name = call.get("name", call.get("tool_name"))
            if not isinstance(name, str) or name not in expected:
                unexpected_calls += 1
                continue
            evidence = _call_evidence(call, provider_calls)
            unlinked_calls += not evidence["model_linked"]
            calls[name].append(evidence)
    offered = (
        set().union(*(item["names"] for item in inventories)) if inventories else set()
    )
    if inventories:
        complete_inventory = all(item["complete"] for item in inventories)
        report["inventory"].update(
            status="CAPTURED" if complete_inventory else "PARTIAL",
            offered=sorted(offered),
            not_offered=sorted(expected - offered) if complete_inventory else [],
            offered_outside_catalogue=sorted(offered - expected),
            sources=sorted({item["source"] for item in inventories}),
        )
        if complete_inventory:
            report["coverage"]["offered_count"] = len(offered)
    for row in report["tools"]:
        row["calls"] = calls[row["name"]]
        statuses = {call["status"] for call in row["calls"]}
        if statuses & {"FAIL", "BLOCKED"}:
            row["status"] = "FAIL" if "FAIL" in statuses else "BLOCKED"
        elif statuses == {"CALL_SUCCEEDED"}:
            row["status"] = "CALL_SUCCEEDED"
        elif statuses:
            row["status"] = "UNKNOWN"
        elif report["inventory"]["status"] == "CAPTURED" and row["name"] not in offered:
            row["status"] = "NOT_OFFERED"
    coverage = report["coverage"]
    coverage.update(
        called=sum(
            any(call["model_linked"] for call in row["calls"])
            for row in report["tools"]
        ),
        succeeded=sum(row["status"] == "CALL_SUCCEEDED" for row in report["tools"]),
    )
    report.update(
        missing_traces=missing_traces,
        trace_error_count=trace_failures,
        unexpected_call_count=unexpected_calls,
        unlinked_executor_call_count=unlinked_calls,
    )
    responses = report["provider"]["responses"]
    provider_failed = any(
        response["status"] in {"incomplete", "failed", "cancelled"}
        or response["error_present"]
        for response in responses
    )
    if provider_failed:
        report["provider"]["status"] = "FAIL"
    elif responses and all(response["status"] == "completed" for response in responses):
        report["provider"]["status"] = "COMPLETED"
    failed = (
        provider_failed
        or trace_failures
        or any(row["status"] in {"FAIL", "BLOCKED"} for row in report["tools"])
    )
    if failed:
        report["status"] = coverage["status"] = "FAIL"
        report["reason"] = (
            "provider_response_not_completed"
            if provider_failed
            else "tool_or_trace_failed"
        )
    elif not coverage["called"]:
        report["reason"] = "no_verified_tool_calls"
    else:
        report["status"] = "PARTIAL"
        complete = (
            coverage["succeeded"] == len(expected)
            and not missing_traces
            and not unexpected_calls
            and not unlinked_calls
            and report["inventory"]["status"] == "CAPTURED"
            and offered == expected
            and report["provider"]["status"] == "COMPLETED"
        )
        coverage["status"] = "PASS" if complete else "PARTIAL"
        if complete:
            report["reason"] = (
                "local_function_coverage_complete; hosted_mcp_and_effects_not_fully_proven"
            )
        else:
            report["reason"] = "active_catalogue_model_call_coverage_incomplete"
    return report
