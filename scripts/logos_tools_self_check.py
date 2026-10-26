"""Judge a fresh Logos tools reply from its invocation evidence, without a model call."""

import hashlib
import math
import re


SELF_CHECK_QUESTION = (
    "Логос, проведи проверку всех инструментов, реально переданных тебе в этом запросе: "
    "локальных, hosted и MCP. Сопоставь их с приложенным каталогом приложения: укажи "
    "отключённые категории, отсутствующие инструменты и причины недоступности. Для "
    "каждого инструмента перечисли параметры из фактической схемы, обязательность, "
    "значения по умолчанию, допустимые значения и пределы; отличай схему от реально "
    "принятых параметров. Выполни безопасные пробные вызовы чтения и проверь результат. "
    "Проверки записи допустимы только на собственных временных обратимых данных. "
    "Не читай секреты, ключи, переписку, контакты и другие личные данные; не выводи их. "
    "Опасные действия, внешние публикации, платные дополнительные сервисы и операции "
    "с устройствами не выполняй: обозначь NOT_RUN и причину. Не меняй доступ, настройки, "
    "защиту и ограничения. Не удаляй ограничения ради прохождения теста. Проверь также "
    "фактические настройки запроса: контекст, вывод, рассуждение, усечение, число "
    "вызовов и раундов, таймауты. Для каждого предела укажи источник: схема, приложение "
    "или провайдер. Неизвестное обозначь UNKNOWN: успешный вызов не доказывает, что "
    "параметры не ограничены. Итог: таблица инструмент → параметры → выполненный вызов "
    "→ результат → ограничение → доказательство. Не утверждай «всё доступно» по одному "
    "каталогу или по собственному ответу без фактических вызовов."
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
_TOOL_TYPES = {"function", "mcp", "file_search", "web_search", "code_interpreter", "other"}


def _name(value):
    return isinstance(value, str) and bool(_NAME.fullmatch(value)) and not _SECRET.search(value)


def _digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _signature(message):
    return tuple(message.get(key) for key in ("role", "text", "invocation_id"))


def _bind_reply(before, after, question):
    if not isinstance(question, str) or not question.strip():
        return [], "question_missing"
    if not isinstance(before, list) or not isinstance(after, list):
        return [], "message_history_unavailable"
    if not all(isinstance(message, dict) for message in before + after):
        return [], "message_history_invalid"
    if len(after) <= len(before) or [_signature(m) for m in after[:len(before)]] != [
        _signature(m) for m in before
    ]:
        return [], "message_history_not_append_only"
    fresh = after[len(before):]
    if fresh[0].get("role") != "user" or fresh[0].get("text") != question:
        return [], "exact_new_question_not_persisted"
    if any(message.get("role") == "user" for message in fresh[1:]):
        return [], "reply_binding_ambiguous"
    replies = [m for m in fresh[1:] if m.get("role") == "assistant"]
    if not replies or any(not isinstance(m.get("text"), str) or not m["text"].strip() for m in replies):
        return [], "fresh_nonempty_reply_missing"
    previous_ids = {m.get("invocation_id") for m in before if isinstance(m.get("invocation_id"), str)}
    ids = []
    for message in replies:
        invocation_id = message.get("invocation_id")
        if not isinstance(invocation_id, str) or not invocation_id or invocation_id in previous_ids:
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
        if not isinstance(manifest, dict) or manifest.get("capture_stage") != "assembled_payload_before_post":
            continue
        request = {"capture_stage": "assembled_payload_before_post", "provider_effective": "UNKNOWN", "settings": {}}
        settings = manifest.get("settings")
        for key in _BOOL_SETTINGS | _NUMBER_SETTINGS | set(_ENUM_SETTINGS) | {"reasoning"}:
            field = settings.get(key) if isinstance(settings, dict) else None
            if (not isinstance(field, dict) or not isinstance(field.get("state"), str)
                    or field["state"] not in {"present", "absent"}):
                continue
            safe = {"state": field["state"]}
            value = field.get("value")
            if field["state"] == "present":
                if key in _BOOL_SETTINGS and type(value) is bool:
                    safe["value"] = value
                elif key in _NUMBER_SETTINGS and (type(value) is int or type(value) is float and math.isfinite(value)):
                    safe["value"] = value
                elif key in _ENUM_SETTINGS and isinstance(value, str) and value in _ENUM_SETTINGS[key]:
                    safe["value"] = value
                elif key == "reasoning" and isinstance(field.get("effort"), str) and field["effort"] in _EFFORT:
                    safe["effort"] = field["effort"]
            request["settings"][key] = safe
        tools = manifest.get("tools")
        if isinstance(tools, dict):
            if type(tools.get("count")) is int and tools["count"] >= 0:
                request["offered_tool_count"] = tools["count"]
            types = tools.get("types")
            if isinstance(types, dict):
                request["offered_tool_types"] = {
                    key: value for key, value in types.items()
                    if key in _TOOL_TYPES and type(value) is int and value >= 0
                }
        result.append(request)
    return result


def _call_evidence(call):
    result = call.get("result")
    result = result if isinstance(result, dict) else {}
    metadata = result.get("metadata")
    phase = metadata.get("phase") if isinstance(metadata, dict) else None
    phase = phase if isinstance(phase, str) and phase in _PHASES else "UNKNOWN"
    if phase in _BLOCKED:
        status = "BLOCKED"
    elif call.get("error") or result.get("error") or result.get("success") is False:
        status = "FAIL"
    elif result.get("success") is True and phase == "execution":
        status = "CALL_SUCCEEDED"
    else:
        status = "UNKNOWN"
    evidence = {"status": status, "phase": phase}
    call_id = call.get("call_id")
    if isinstance(call_id, str) and call_id:
        evidence["call_id_sha256"] = _digest(call_id)
    else:
        evidence["status"] = "UNKNOWN"
        evidence["reason"] = "model_call_link_missing"
    return evidence


def evaluate_tool_evidence(categories, active_categories, before_messages, after_messages, traces, question):
    """Return conservative coverage; prose never establishes execution or unbounded limits.

    ``traces`` maps fresh assistant invocation IDs to the corresponding trace JSON.
    A successful envelope establishes one completed call only, not its real effect,
    every argument combination, or unlimited provider/adapter capabilities.
    """
    report = {
        "status": "NOT_RUN", "tools": [], "inactive_tools": [],
        "coverage": {"status": "NOT_RUN", "expected": 0, "called": 0, "succeeded": 0,
                     "scope": "fresh_invocation_call_envelopes_only; effects_not_independently_verified"},
        "limits": {"status": "UNKNOWN", "unbounded": "UNKNOWN", "parameter_domains": "UNKNOWN",
                   "provider_effective": "UNKNOWN", "requests": [],
                   "reason": "finite_calls_do_not_prove_unbounded_parameters"},
        "hosted_and_mcp": {"status": "UNKNOWN", "reason": "registry_categories_do_not_prove_hosted_or_remote_inventory"},
    }
    if (not isinstance(categories, dict) or not categories or not isinstance(active_categories, list)
            or any(not _name(category) or not isinstance(names, list) or any(not _name(name) for name in names)
                   for category, names in categories.items())
            or any(not isinstance(category, str) or category not in categories for category in active_categories)):
        report.update(status="ERROR", reason="tool_inventory_invalid_or_unavailable")
        return report
    active = set(active_categories)
    expected = {name for category, names in categories.items() if category in active for name in names}
    inactive = {name for category, names in categories.items() if category not in active for name in names} - expected
    report["inactive_tools"] = sorted(inactive)
    report["coverage"]["expected"] = len(expected)
    report["tools"] = [{"name": name, "status": "NOT_RUN", "calls": []} for name in sorted(expected)]
    ids, reason = _bind_reply(before_messages, after_messages, question)
    if reason:
        report["reason"] = reason
        return report
    if not expected:
        report["reason"] = "no_active_tools"
        return report
    report["binding"] = {"status": "BOUND", "question_sha256": _digest(question),
                         "invocation_id_sha256": [_digest(value) for value in ids]}
    calls = {name: [] for name in expected}
    trace_failures = 0
    missing_traces = 0
    unexpected_calls = 0
    for invocation_id in ids:
        trace = traces.get(invocation_id) if isinstance(traces, dict) else None
        context = trace.get("context") if isinstance(trace, dict) else None
        if not isinstance(context, dict) or context.get("invocation_id") != invocation_id:
            missing_traces += 1
            continue
        errors = trace.get("errors")
        trace_failures += len(errors) if isinstance(errors, list) else 0
        report["limits"]["requests"].extend(_request_evidence(trace))
        entries = trace.get("tool_calls")
        for call in entries if isinstance(entries, list) else []:
            if not isinstance(call, dict):
                continue
            name = call.get("name", call.get("tool_name"))
            if not isinstance(name, str) or name not in expected:
                unexpected_calls += 1
                continue
            calls[name].append(_call_evidence(call))
    for row in report["tools"]:
        row["calls"] = calls[row["name"]]
        statuses = {call["status"] for call in row["calls"]}
        if statuses & {"FAIL", "BLOCKED"}:
            row["status"] = "FAIL" if "FAIL" in statuses else "BLOCKED"
        elif statuses == {"CALL_SUCCEEDED"}:
            row["status"] = "CALL_SUCCEEDED"
        elif statuses:
            row["status"] = "UNKNOWN"
    coverage = report["coverage"]
    coverage.update(called=sum(bool(row["calls"]) for row in report["tools"]),
                    succeeded=sum(row["status"] == "CALL_SUCCEEDED" for row in report["tools"]))
    report.update(missing_traces=missing_traces, trace_error_count=trace_failures,
                  unexpected_call_count=unexpected_calls)
    failed = trace_failures or any(row["status"] in {"FAIL", "BLOCKED"} for row in report["tools"])
    if failed:
        report["status"] = coverage["status"] = "FAIL"
    elif not coverage["called"]:
        report["reason"] = "no_verified_tool_calls"
    else:
        report["status"] = "PARTIAL"
        coverage["status"] = "PASS" if coverage["succeeded"] == len(expected) and not missing_traces else "PARTIAL"
        report["reason"] = "tool_calls_do_not_prove_full_inventory_or_unbounded_parameters"
    return report
