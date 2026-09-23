"""Маршруты чата с сохранением полной цепочки выполнения в БД."""
from flask import Blueprint, request, jsonify
import logging
import uuid
import json as _json
from yandex_client import YandexResponsesClient
from config import Config, calculate_full_cost
from trace_manager import ExecutionTrace
from invocation_manager import (
    create_invocation,
    start_invocation,
    finish_invocation,
    fail_invocation,
    persist_invocation_trace,
)
from invocation_trace import create_invocation_trace
from mcp_trace import record_yandex_mcp_activity
import mcp_storage
from tool_registry import registry
from db import (
    get_conversations, create_conversation, get_messages, add_message,
    get_conv_settings, save_conv_settings
)
from partial_output import extract_last_response_text, format_partial_output_message
from responses_tool_loop import run_tool_loop, extract_function_calls
from billing import settle_billing_to_treasury
from treasury_identity import get_current_owner_id
from universal_tool_platform import UniversalToolCall, UniversalToolExecutor
from conversation_ownership import (
    init_conversation_ownership_table,
    set_owner,
    check_access,
    list_owned_conversations,
    get_owned_conversation,
    delete_owner,
)

logger = logging.getLogger("mcp_routes")
mcp_bp = Blueprint('mcp', __name__)


class AliceClient(YandexResponsesClient):
    def ask_with_mcp(self, message, model_key, conversation_id=None, params=None, trace=None):
        response = super().ask_with_mcp(
            message=message,
            model_key=model_key,
            conversation_id=conversation_id,
            params=params,
            trace=trace,
        )

        calls = extract_function_calls(response)
        if calls:
            approval_required = any(
                (registry.get_tool_meta(
                    (call.get("name") or call.get("function", {}).get("name") or "").split("<|", 1)[0].strip()
                ) or {}).get("requires_approval")
                for call in calls
            )

            if not approval_required:
                tool_servers = mcp_storage.list_servers()
                base_params = dict(params or {})

                def execute_tool(call):
                    result = self._execute_single_tool(call, tool_servers, trace=trace)
                    if result.get("error"):
                        return {"error": result["error"], "tool": result.get("name")}
                    return result.get("result")

                def continue_request(input_items, previous_response):
                    continuation_params = dict(base_params)
                    continuation_params["input"] = input_items
                    continuation_params["background"] = base_params.get("background", True)
                    return super(AliceClient, self).ask_with_mcp(
                        message="",
                        model_key=model_key,
                        conversation_id=conversation_id,
                        params=continuation_params,
                        trace=trace,
                    )

                response = run_tool_loop(
                    response,
                    execute_tool,
                    continue_request,
                    max_rounds=16,
                )

        if trace is not None:
            record_yandex_mcp_activity(trace)
        return response


@mcp_bp.route('/api/chat', methods=['POST'])
def chat():
    import time as _time
    t_start = _time.perf_counter()
    trace = None
    invocation = None
    trace_data = {}
    conv_id = None
    session_id = None
    message = None
    model_key = "aliceai-llm"
    partial_output = None
    error_message = None

    try:
        data = request.get_json(silent=True) or {}
        conv_id = data.get('conversation_id')
        session_id = data.get('session_id') or conv_id
        message = data.get('message')
        params = data.get('params', {})
        model_key = data.get('model') or params.get('model', 'aliceai-llm')

        if not conv_id or not message:
            return jsonify({"error": "conversation_id и message обязательны"}), 400

        owner_id = get_current_owner_id(required=False)
        invocation = create_invocation(
            session_id,
            conv_id,
            metadata={"model": model_key},
            user_id=owner_id,
        )
        trace = create_invocation_trace(invocation)
        start_invocation(invocation.invocation_id)
        trace.set_request({
            "conversation_id": invocation.conversation_id,
            "message": message,
            "model": model_key,
            "params": params,
            "invocation_id": invocation.invocation_id,
            "session_id": invocation.session_id,
            "trace_id": invocation.trace_id,
        })

        conv_settings = get_conv_settings(invocation.conversation_id) or {}
        active_tools = conv_settings.get("active_tool_categories")
        if active_tools is not None:
            params["active_tool_categories"] = active_tools

        add_message(invocation.conversation_id, "user", message)
        client = AliceClient(Config)

        response = client.ask_with_mcp(
            message=message,
            model_key=model_key,
            conversation_id=invocation.conversation_id,
            params=params,
            trace=trace
        )

        api_requests = trace.trace.get("api_requests", [])
        if api_requests:
            first_api_start = api_requests[0].get("timestamp")
            request_init = next(
                (event for event in trace.trace.get("events", [])
                 if event.get("type") == "request_initialized"),
                None
            )
            request_init_timestamp = request_init.get("timestamp") if request_init else None
            if isinstance(request_init_timestamp, (int, float)) and isinstance(first_api_start, (int, float)):
                pre_api_ms = round(max(0.0, first_api_start - request_init_timestamp) * 1000, 2)
                trace.trace.setdefault("timings", {})["pre_api_pipeline"] = {
                    "start_timestamp": request_init_timestamp,
                    "end_timestamp": first_api_start,
                    "duration_ms": pre_api_ms
                }
                trace.trace.setdefault("events", []).append({
                    "type": "pre_api_pipeline_completed",
                    "timestamp": first_api_start,
                    "payload": {
                        "start_timestamp": request_init_timestamp,
                        "end_timestamp": first_api_start,
                        "timing_ms": pre_api_ms,
                        "step": api_requests[0].get("step", 1)
                    }
                })

        output = response.get("output", []) if isinstance(response, dict) else []
        for item in output:
            calls = []
            if isinstance(item, dict):
                if item.get("type") in ("function_call", "tool_call"):
                    calls.append(item)
                elif item.get("type") == "message":
                    calls.extend([p for p in item.get("content", []) if isinstance(p, dict) and p.get("type") in ("function_call", "tool_call")])

            for tc in calls:
                func_name = tc.get("name") or tc.get("function", {}).get("name")
                if func_name and "<|" in func_name:
                    func_name = func_name.split("<|")[0].strip()
                tool_config = registry.get_tool_meta(func_name)

                if tool_config and tool_config.get("requires_approval"):
                    raw_args = tc.get("arguments") or tc.get("function", {}).get("arguments", {})
                    if isinstance(raw_args, str):
                        try:
                            raw_args = _json.loads(raw_args)
                        except Exception:
                            raw_args = {}

                    trace_data = trace.finalize()
                    persist_invocation_trace(invocation.invocation_id, trace_data)
                    return jsonify({
                        "requires_approval": True,
                        "tool_call": {
                            "name": func_name,
                            "description": tool_config.get("description", func_name),
                            "arguments": raw_args,
                            "call_id": tc.get("call_id") or tc.get("id") or func_name
                        },
                        "original_message": message,
                        "invocation_id": invocation.invocation_id,
                        "session_id": invocation.session_id,
                        "conversation_id": invocation.conversation_id,
                        "trace_id": invocation.trace_id,
                        "trace": trace_data
                    })

        reasoning, reply = client.extract_reasoning_and_text(response)
        if not reply:
            raise RuntimeError("Responses API завершил tool-call цепочку без текстового ответа")
        usage = client.extract_usage(response)
        timings = response.get("step_timings", []) if isinstance(response, dict) else []
        total_ms = round((_time.perf_counter() - t_start) * 1000)
        trace_data = trace.finalize()

        try:
            settlement = settle_billing_to_treasury(
                trace_data.get("billing") or {},
                invocation.user_id,
            )
        except Exception as settlement_error:
            trace.record_error("treasury_settlement", str(settlement_error), exception=settlement_error)
            settlement = {"status": "failed", "reason": str(settlement_error)}
            trace_data = trace.finalize()

        trace_data.setdefault("billing", {})["settlement"] = settlement
        billing = trace_data.get("billing") or {}
        cost = float(billing.get("total_cost") or 0) if billing.get("cost_status") in {"calculated", "partial"} else 0.0

        persist_invocation_trace(invocation.invocation_id, trace_data)
        add_message(invocation.conversation_id, "assistant", str(reply), cost=cost, timings=timings, trace=trace_data)
        finish_invocation(
            invocation.invocation_id,
            result={
                "reply": reply,
                "usage": usage,
                "cost": cost,
                "settlement": settlement,
            },
        )

        return jsonify({
            "reply": reply,
            "usage": usage,
            "cost": cost,
            "timings": timings,
            "total_duration_ms": total_ms,
            "reasoning": reasoning,
            "invocation_id": invocation.invocation_id,
            "session_id": invocation.session_id,
            "conversation_id": invocation.conversation_id,
            "trace_id": invocation.trace_id,
            "trace": trace_data
        })
    except Exception as e:
        logger.exception(f"[CHAT] Ошибка: {e}")
        error_message = str(e)

        try:
            if trace is None:
                if conv_id:
                    invocation = create_invocation(session_id or conv_id, conv_id, metadata={"model": model_key})
                    trace = create_invocation_trace(invocation)
                    start_invocation(invocation.invocation_id)
                else:
                    trace = ExecutionTrace()
            trace.record_error("chat_pipeline", error_message, exception=e)
            record_yandex_mcp_activity(trace)
            trace_data = trace.finalize()
            responses = trace_data.get("responses", [])
            partial_output, _ = extract_last_response_text(responses)
            reply = format_partial_output_message(partial_output, error_message)

            if invocation is not None:
                persist_invocation_trace(invocation.invocation_id, trace_data)
            if conv_id:
                add_message(conv_id, "assistant", reply, trace=trace_data)
            if invocation is not None:
                fail_invocation(invocation.invocation_id, error={"message": error_message})

            return jsonify({
                "error": error_message,
                "reply": reply,
                "partial_output": partial_output if partial_output else None,
                "invocation_id": invocation.invocation_id if invocation else None,
                "session_id": invocation.session_id if invocation else None,
                "conversation_id": invocation.conversation_id if invocation else conv_id,
                "trace_id": invocation.trace_id if invocation else trace.trace_id,
                "trace": trace_data
            }), 500

        except Exception as inner_e:
            logger.exception("[CHAT] Не удалось сохранить ExecutionTrace")
            return jsonify({
                "error": error_message,
                "reply": f"⚠️ Ошибка: {error_message}",
                "partial_output": None,
                "trace": {}
            }), 500

@mcp_bp.route('/api/tools/categories', methods=['GET'])
def list_tool_categories():
    return jsonify({
        "categories": registry.get_available_categories()
    })

@mcp_bp.route('/api/conversations/<conv_id>/tools', methods=['GET', 'PUT'])
def conv_tools(conv_id):
    if request.method == 'PUT':
        data = request.get_json(silent=True) or {}
        categories = data.get("active_tool_categories", [])
        settings = get_conv_settings(conv_id) or {}
        settings["active_tool_categories"] = categories
        save_conv_settings(conv_id, settings)
        return jsonify({"status": "ok", "active_tool_categories": categories})

    settings = get_conv_settings(conv_id) or {}
    cats = settings.get("active_tool_categories")
    if cats is None:
        cats = list(registry.get_available_categories().keys())
    return jsonify({"active_tool_categories": cats})

@mcp_bp.route('/api/mcp-servers', methods=['GET', 'POST'])
def handle_mcp_servers():
    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        sid = mcp_storage.create_server(data)
        return jsonify({"status": "ok", "id": sid}), 201
    return jsonify({"data": mcp_storage.list_servers()})

@mcp_bp.route('/api/mcp-servers/<server_id>', methods=['GET', 'PUT', 'DELETE'])
def handle_mcp_server_item(server_id):
    if request.method == 'DELETE':
        mcp_storage.delete_server(server_id)
        return jsonify({"status": "deleted"})
    if request.method == 'PUT':
        data = request.get_json(silent=True) or {}
        mcp_storage.update_server(server_id, data)
        return jsonify({"status": "updated"})

    srv = mcp_storage.get_server(server_id)
    if not srv:
        return jsonify({"error": "Сервер не найден"}), 404
    return jsonify(srv)

@mcp_bp.route('/api/conversations', methods=['GET', 'POST'])
def conversations():
    owner_id = get_current_owner_id(required=False)
    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        client = AliceClient(Config)
        try:
            y_conv = client.create_conversation()
            conv_id = y_conv.get('id') or str(uuid.uuid4())
        except Exception:
            conv_id = str(uuid.uuid4())
        title = data.get('title') or 'Новый чат'
        model = data.get('model', 'aliceai-llm')
        create_conversation(conv_id, title, model)
        if owner_id:
            set_owner(conv_id, owner_id)
        return jsonify({"id": conv_id, "title": title, "model": model}), 201

    if owner_id:
        return jsonify({"conversations": list_owned_conversations(owner_id)})
    return jsonify({"conversations": get_conversations()})


@mcp_bp.route('/api/conversations/<conv_id>/messages', methods=['GET'])
def get_conv_messages(conv_id):
    owner_id = get_current_owner_id(required=False)
    if owner_id and not check_access(conv_id, owner_id):
        return jsonify({"error": "conversation_not_found"}), 404
    return jsonify({"messages": get_messages(conv_id)})

@mcp_bp.route('/api/mcp/execute-approved', methods=['POST'])
def execute_approved():
    try:
        data = request.get_json(silent=True) or {}
        conv_id = data.get("conversation_id")
        func_name = data.get("name")
        arguments = data.get("arguments", {})
        model_key = data.get("model", "aliceai-llm")

        tool_config = registry.get_tool_meta(func_name)
        if not tool_config:
            return jsonify({"error": f"Неизвестный инструмент: {func_name}"}), 400

        owner_id = get_current_owner_id(required=False)
        call = UniversalToolCall(
            tool_name=func_name,
            arguments=arguments,
            transport="internal",
            user_id=owner_id,
            approved=True,
            metadata={"source": "approved_action"},
        )
        exec_res = UniversalToolExecutor(registry).execute(call)
        if not exec_res.get("success"):
            return jsonify({
                "error": exec_res.get("error") or "Tool execution failed",
                "execution_result": exec_res,
            }), 403 if (exec_res.get("metadata") or {}).get("phase") in {"authorization", "approval_required"} else 400

        execution_data = exec_res.get("data")
        client = AliceClient(Config)
        prompt = (
            f"Пользователь подтвердил действие '{func_name}' с параметрами {arguments}.\n"
            f"Результат: {execution_data}.\nДай краткий ответ о завершении."
        )
        synth_response = client.ask(prompt, model_key, conv_id, {"instructions": "Ты системный ассистент."})
        reply = client.extract_text(synth_response) or f"Действие {func_name} успешно выполнено."
        usage = client.extract_usage(synth_response)
        cost = calculate_full_cost(model_key, usage) if usage else 0.0

        add_message(conv_id, "assistant", reply, cost=cost)
        return jsonify({"reply": reply, "cost": cost, "execution_result": exec_res})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
