"""MCP mixin of the Responses client: requests with MCP and hosted tools (ask_with_mcp), including file search."""

import json
from typing import Any


from yandex_client_modules.request_utils import sanitize_for_log as _sanitize_for_log
from yandex_client_modules.api_logger import api_logger
from yandex_client_modules.request_builder import response_input
from yandex_client_modules.tool_config import (
    deferred_tool_categories,
    file_search_max_results,
    global_settings,
    knowledge_vector_store_ids,
    normalize_tools_config,
)
from yandex_client_modules.host import (
    bound,
    get_controlled_memory_summary,
    get_current_owner_id,
)


def _max_results(value: Any) -> int:
    """File-search result count from settings, clamped to the provider's range."""
    return file_search_max_results(value)


# Fixed text ahead of the owner's instructions: who the model is and how it works here.
# Without it the model got "Ты — полезный ассистент." and spent 22 shell calls reading its
# own source to learn that SSH Runtime was off (invocation b432fd32: 24 rounds, 247 RUB).
# It is constant, so the cached prompt prefix stays the same from turn to turn.
BASE_INSTRUCTIONS = (
    "Ты — Логос, ассистент владельца этого приложения. Приложение Logos работает на "
    "телефоне Redmi 9 в Termux: код в /data/data/com.termux/files/home/logos-local, "
    "бэкенд отвечает на http://127.0.0.1:8080. Владелец общается по-русски и ждёт "
    "действия, а не плана.\n"
    "Как работать.\n"
    "1. Инструменты вызывай только через function calling. Никогда не пиши вызов "
    "текстом ответа и никакой служебной разметкой.\n"
    "2. Сначала вызови инструмент, который решает задачу; его ответ сам скажет, чего не "
    "хватает. Не изучай исходный код приложения, чтобы понять, как оно настроено: "
    "настройки видны в его API (например GET /api/ssh-runtime/settings) и в ответах "
    "инструментов.\n"
    "3. Не хватает параметра, доступа или настройки — скажи это одним коротким "
    "сообщением: что именно нужно и где это задаётся. Не ищи значение сам, не "
    "подставляй придуманное, не читай окружение процессов (/proc/*/environ) и файлы "
    "с секретами.\n"
    "4. Серверы по SSH: цели задаются владельцем в окне SSH Runtime; если целей нет или "
    "runtime выключен, назови это блокером и остановись.\n"
    "5. Куда идти за задачей: сертификаты и веб-сервер — ssl; команды на этом телефоне — "
    "runtime (local_runtime_exec); команды на серверах — runtime (ssh_runtime_exec) и "
    "ssl; файлы репозитория — filesystem и git; телефон — system и rootagent; браузер — "
    "browser; разбирательства — court; облако — cloud; загрузить репозиторий и GitHub "
    "в базу знаний (RAG) — rag (action sync, ход — status); своя векторная база "
    "приложения (эмбеддинги, поиск по смыслу) — vectors.\n"
    "6. Отвечай коротко: что сделано, что получилось, что нужно от владельца. "
    "Без заголовков-формул и без пересказа своих поисков.\n"
    "7. Блокер сообщай честно и сразу; не имитируй работу обходными действиями.\n\n"
)

# Without a vector store the model has no search tool and used to answer "no access" (#1651).
FILE_SEARCH_OFF_NOTE = (
    "\n\nПоиск по векторным хранилищам (RAG) и архиву репозитория в этом диалоге не подключён: "
    "в настройках диалога не включён File Search или не выбран Vector Store ID. "
    "Если пользователь просит поискать в RAG или архиве, так и скажи и укажи путь: "
    "Инструменты → File Search (поиск по Vector Store) → Vector Store IDs. "
    "Не предлагай поиск вручную вместо этого."
)

# The hosted file_search tool carries no description, so the model did not
# connect it with "память", "индекс" or "RAG" and answered from the web (#1660).
FILE_SEARCH_ON_NOTE = (
    "\n\nВ этом диалоге подключён поиск по векторному хранилищу (File Search, RAG; "
    "инструмент file_search, он же search_index): в нём архив диалогов с Логосом, "
    "issues и документы репозитория владельца. Вызывай file_search, когда вопрос "
    "касается прошлых диалогов, памяти, issues или документов владельца и всегда, когда "
    "пользователь просит найти что-то в памяти, в индексе, в RAG, в архиве, в диалогах "
    "или файлах; опирайся на найденные фрагменты, называя файлы-источники (filename). "
    # Invocation b432fd32: a forced search on every turn spent 178 655 tool tokens on a
    # certificate request and found nothing usable.
    "Для действия инструментами, настроек приложения и вопросов, не связанных с архивом, "
    "индекс не нужен. "
    "Не подменяй найденное общими сведениями и веб-поиском; если в индексе ничего "
    "подходящего нет, так и скажи. "
    # #1669: the model presented facts from the user's own message as index findings,
    # with sources that do not contain them.
    "Говори «в индексе есть» только о том, что дословно содержится в найденных "
    "фрагментах, и называй файл именно того фрагмента, где это написано. Сказанное "
    "пользователем в его сообщении — не находка в индексе: не пересказывай его как "
    "найденное и не приписывай ему источник. Не придумывай даты, номера комментариев "
    "и имена файлов. Если найденное лишь похоже по словам, а сути вопроса не "
    "содержит, так и скажи."
)

class YandexMcpMixin:
    def _execute_single_tool(
        self, tc, all_servers, trace=None, *, execution_policy=None
    ):
        import time as _t

        function = tc.get("function")
        if not isinstance(function, dict):
            function = {}
        name = tc.get("name") or function.get("name")

        args = tc["arguments"] if "arguments" in tc else function.get("arguments", {})

        call_id = tc.get("call_id") or tc.get("id") or tc.get("tool_call_id") or name

        start_timestamp = _t.time()
        t_start = _t.perf_counter()

        trace_id = trace.trace_id if isinstance(trace, bound.ExecutionTrace) else None
        user_id = None
        invocation_id = None
        if isinstance(trace, bound.ExecutionTrace):
            user_id = (trace.trace.get("context") or {}).get("user_id")
            invocation_id = (trace.trace.get("context") or {}).get("invocation_id")

        try:
            try:
                if not isinstance(name, str) or not name.strip():
                    raise ValueError("Invalid tool name: expected a non-empty string")
                name = name.split("<|", 1)[0].strip()
                if not name:
                    raise ValueError("Invalid tool name: expected a non-empty string")
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except ValueError:
                        raise ValueError(
                            "Invalid tool arguments: expected a valid JSON object"
                        ) from None
                if not isinstance(args, dict):
                    raise ValueError("Invalid tool arguments: expected a JSON object")
                # A tool's one function names its action in "tool" (one tool, one function).
                inner_name, args = bound.registry.unwrap_call(name, args)
                if inner_name != name and isinstance(trace, bound.ExecutionTrace):
                    trace.add_event(
                        "tool_group_call",
                        {"group": name, "tool": inner_name, "call_id": call_id},
                    )
                name = inner_name
                if not isinstance(args, dict):
                    raise ValueError("Invalid tool arguments: expected a JSON object")
            except ValueError as exc:
                if isinstance(trace, bound.ExecutionTrace):
                    trace.add_event(
                        "tool_arguments_rejected",
                        {"tool": name, "call_id": call_id, "error": str(exc)},
                    )
                raise

            call = bound.UniversalToolCall(
                tool_name=name,
                arguments=args,
                call_id=call_id,
                transport="responses_api",
                trace_id=trace_id,
                invocation_id=invocation_id,
                user_id=user_id,
                metadata={
                    "source": "yandex_responses",
                    "server_count": len(all_servers or []),
                },
            )
            executor = bound.UniversalToolExecutor(bound.registry)
            if isinstance(trace, bound.ExecutionTrace):
                universal_result = executor.execute_with_trace(
                    call, trace, server_configs=all_servers
                )
            else:
                universal_result = executor.execute(call, server_configs=all_servers)

            if universal_result.get("success"):
                result = universal_result.get("data")
                error = None
            else:
                result = None
                error = universal_result.get("error") or "Tool execution failed"
            api_logger.debug(
                "[LOCAL TOOL RESULT] name=%s call_id=%s\\n%s",
                name,
                call_id,
                json.dumps(
                    _sanitize_for_log(result), ensure_ascii=False, indent=2, default=str
                )
                if isinstance(result, (dict, list))
                else str(result),
            )

        except Exception as exc:
            result = None
            error = str(exc) or type(exc).__name__

            api_logger.exception("[LOCAL TOOL ERROR] name=%s call_id=%s", name, call_id)

        t_end = _t.perf_counter()
        end_timestamp = _t.time()

        duration_ms = round((t_end - t_start) * 1000, 2)

        content_str = (
            json.dumps(result, ensure_ascii=False, default=str)
            if isinstance(result, (dict, list))
            else str(result)
            if result is not None
            else ""
        )

        timing = {
            "name": f"Tool: {name}",
            "duration_ms": duration_ms,
            "server_type": "local",
            "server_label": "Local Registry",
            "start_timestamp": start_timestamp,
            "end_timestamp": end_timestamp,
            "success": error is None,
        }

        return {
            "call_id": call_id,
            "name": name,
            "content": content_str,
            "result": result,
            "error": error,
            "timing": timing,
        }

    def ask_with_mcp(
        self,
        message: Any,
        model_key: Any,
        conversation_id: Any = None,
        params: Any = None,
        trace: Any = None,
        *,
        execution_policy: Any | None = None,
        settings_conversation_id: Any = None,
    ) -> Any:
        params = params or {}

        mcp_tools = []
        # The chat's own id keys its settings and enabled servers. The provider conversation is
        # only the thread id, and it differs from the chat's id for every new chat.
        settings_id = settings_conversation_id or conversation_id
        # Shared conversations use the installation's MCP configuration, including
        # conversations created before ownership metadata existed.
        owner_id = get_current_owner_id()
        def note(source: str, message: str) -> None:
            """A part of the turn left out on purpose is recorded, not dropped silently."""
            if trace is not None and isinstance(trace, bound.ExecutionTrace):
                trace.record_error(source, message)

        enabled_servers = []
        if settings_id:
            # Settings are global: every configured server serves every dialog. A failed
            # read must not turn into "no MCP servers": the turn fails with the cause.
            enabled_servers = bound.mcp_storage.list_servers(owner_id=owner_id)

        for s in enabled_servers if execution_policy is None else []:
            if (s.get("config") or {}).get("requires_authorization") and not s.get(
                "authorization"
            ):
                # No token yet (e.g. the seeded RDC record): every call would be refused.
                note(
                    "mcp_servers",
                    f"MCP server {s.get('server_label') or s.get('name')} left out: no token",
                )
                continue
            if s.get("server_url") or (s.get("connector_id") or "").startswith(
                "connector_"
            ):
                mcp_tool = {
                    "type": "mcp",
                    "server_label": s.get("server_label") or s.get("name"),
                    "server_url": s.get("server_url"),
                    "connector_id": s.get("connector_id"),
                    "authorization": s.get("authorization"),
                    "require_approval": "never",
                }
                if s.get("server_description"):
                    mcp_tool["server_description"] = s["server_description"]
                # Defer loading is always on across the whole tool surface: an MCP
                # server's tools, like the local ones, wait for the provider's tool
                # search instead of loading eagerly.
                mcp_tool["defer_loading"] = True
                mcp_tools.append(mcp_tool)

        # Settings are global (no per-dialog record): a dialog turn uses every category.
        active_cats = None if settings_id else params.get("active_tool_categories")
        if not isinstance(active_cats, list):
            active_cats = None
        if active_cats is None:
            # Same default as the tools panel: every registered category. The
            # registry offers only tools this transport and host can execute.
            active_cats = list(bound.registry.get_available_categories())

        hosted_tools = []
        # A dialog turn takes the global settings; a turn without a dialog its params.
        conv_settings = global_settings() if settings_id else {}
        raw_tools_config = conv_settings.get(
            "tools_config", params.get("tools_config", {})
        )
        tools_config = normalize_tools_config(raw_tools_config)

        raw_web_cfg = tools_config.get("web_search")
        web_cfg = raw_web_cfg or {}
        web_enabled = True if raw_web_cfg is None else web_cfg.get("enabled", False)
        if web_enabled:
            web_tool = {
                "type": "web_search",
                "search_context_size": web_cfg.get("context_size") or "high",
            }
            allowed_domains = list(web_cfg.get("allowed_domains", []))
            blocked_domains = list(web_cfg.get("blocked_domains", []))
            # A project-wide exclusion is not removable through conversation settings.
            required_blocks = tuple(getattr(self, "mandatory_blocked_web_domains", ()))
            if required_blocks:
                normalized_blocks = {
                    domain.casefold().rstrip(".") for domain in required_blocks
                }
                allowed_domains = [
                    domain
                    for domain in allowed_domains
                    if not any(
                        domain.casefold().removeprefix("*.").rstrip(".") == block
                        or domain.casefold()
                        .removeprefix("*.")
                        .rstrip(".")
                        .endswith("." + block)
                        for block in normalized_blocks
                    )
                ]
                seen_blocks = {
                    domain.casefold().rstrip(".") for domain in blocked_domains
                }
                for domain in required_blocks:
                    if domain not in seen_blocks:
                        blocked_domains.append(domain)
                        seen_blocks.add(domain)
            if allowed_domains or blocked_domains:
                web_tool["filters"] = {}
                if allowed_domains:
                    web_tool["filters"]["allowed_domains"] = allowed_domains
                if blocked_domains:
                    web_tool["filters"]["blocked_domains"] = blocked_domains
            hosted_tools.append(web_tool)

        # Hosted tools are on unless the dialog switched them off, like web search.
        raw_file_cfg = tools_config.get("file_search")
        file_cfg = raw_file_cfg or {}
        file_enabled = True if raw_file_cfg is None else file_cfg.get("enabled", False)
        # Every dialog searches the owner's knowledge store, plus any store it names.
        vector_store_ids = list(
            dict.fromkeys(
                knowledge_vector_store_ids()
                + list(file_cfg.get("vector_store_ids") or [])
            )
        )
        if file_enabled:
            hosted_tools.append(
                {
                    "type": "file_search",
                    "vector_store_ids": vector_store_ids,
                    "max_num_results": _max_results(file_cfg.get("max_results")),
                }
            )

        file_search_active = bool(file_enabled and vector_store_ids)
        raw_code_cfg = tools_config.get("code_interpreter")
        code_cfg = raw_code_cfg or {}
        code_enabled = True if raw_code_cfg is None else code_cfg.get("enabled", False)
        if code_enabled:
            hosted_tools.append(
                {"type": "code_interpreter", "container": {"type": "auto"}}
            )

        tools = mcp_tools + hosted_tools if execution_policy is None else []

        # One tool, one function: each category goes as one function named after it,
        # whose "tool" field picks the action. The registry offers only the actions
        # this transport and host can run; a policy narrows them further.
        sent_local: list[dict[str, Any]] = []
        actions_offered = 0
        # Which tool each action sits in, for a tool_choice that names an action.
        grouped_into: dict[str, str] = {}
        allow_action = None if execution_policy is None else execution_policy.allows_tool
        defer_on, eager_categories = deferred_tool_categories(tools_config)
        for category in active_cats:
            try:
                actions = bound.registry.get_actions(category, allow_action=allow_action)
                if not actions:
                    continue
                functions = bound.registry.get_tools_by_category(
                    category, allow_action=allow_action
                )
                if defer_on and category not in eager_categories:
                    functions = [{**tool, "defer_loading": True} for tool in functions]
                sent_local.extend(functions)
                actions_offered += len(actions)
                grouped_into.update({action: category for action in actions})
            except Exception as e:
                api_logger.error(f"[TOOLS] Ошибка категории {category}: {e}")
                note("tool_category", f"tool category {category} left out: {e}")

        if sent_local and trace is not None and isinstance(trace, bound.ExecutionTrace):
            trace.add_event(
                "tools_grouped_by_category",
                {
                    "tools": actions_offered,
                    "functions": len(sent_local),
                    "other_tools": len(tools),
                },
            )
        tools.extend(sent_local)

        # The server owns the tool list: a caller's "tools" entry never reaches the provider.
        ask_params = dict(params)
        ask_params.pop("tools", None)
        # The provider caches a prompt whose beginning repeats an earlier request's (#1742).
        # The instructions come before the tools, so they carry only fixed text; the
        # memory block, which changes after every turn, goes into the input below.
        if settings_id and execution_policy is None:
            ask_params["instructions"] = (
                BASE_INSTRUCTIONS
                + (ask_params.get("instructions") or "")
                + (FILE_SEARCH_ON_NOTE if file_search_active else FILE_SEARCH_OFF_NOTE)
            )
        # Global memory reaches chat turns only, as a system message ahead of the user's: in
        # the instructions a new fact changed the prefix and every turn paid full price for
        # the instructions and tools (invocation e31d1b90: 256 of 44800 tokens cached).
        # Settings (enabled, facts limit, categories) are applied inside the summary builder.
        # The same message names the chat itself: court and other tools take its
        # conversation_id, and without it the model spent rounds looking for it
        # (invocation 9114cb20: 7 rounds, 70 RUB).
        if settings_id and execution_policy is None:
            try:
                memory_summary = get_controlled_memory_summary()
            except Exception as e:
                api_logger.exception("[MEMORY] Память не подключена к ответу")
                note("memory", f"memory left out of the reply: {type(e).__name__}: {e}")
                memory_summary = ""
            ask_params["input"] = [
                {
                    "role": "system",
                    "content": f"[ТЕКУЩИЙ ДИАЛОГ]\nconversation_id этого диалога Logos: "
                    f"{settings_id}\n{memory_summary.strip()}".strip(),
                },
                *response_input(ask_params, message),
            ]
        if tools or execution_policy is not None:
            ask_params["tools"] = tools
        if not tools and ask_params.get("tool_choice") == "required":
            # Nothing to call (every tool switched off): a required call cannot be met.
            ask_params["tool_choice"] = "auto"
            note("tool_choice", "tool_choice required released to auto: no tool in this turn")
        elif ask_params.get("tool_choice") == "required" and not any(
            tool.get("type") in ("function", "mcp") for tool in tools
        ):
            # Alice AI LLM answered without any call when only hosted tools came with
            # "required" (#1660, invocation a0fc6a4a). Name the hosted tool instead,
            # File Search first.
            hosted_types = [tool.get("type") for tool in tools]
            ask_params["tool_choice"] = {
                "type": "file_search" if "file_search" in hosted_types else hosted_types[0]
            }
        choice = ask_params.get("tool_choice")
        if (
            isinstance(choice, dict)
            and choice.get("type") == "function"
            and choice.get("name") in grouped_into
            and grouped_into[choice["name"]] != choice["name"]
        ):
            # The chosen action sits inside its tool's one function: force that function
            # and say which action of it, so the choice is not widened to the tool.
            ask_params["tool_choice"] = {**choice, "name": grouped_into[choice["name"]]}
            ask_params["instructions"] = (ask_params.get("instructions") or "") + (
                f"\n\nВызови {grouped_into[choice['name']]} с tool=\"{choice['name']}\"."
            )
            if trace is not None and isinstance(trace, bound.ExecutionTrace):
                trace.add_event(
                    "tool_choice_grouped",
                    {"tool": choice["name"], "group": grouped_into[choice["name"]]},
                )

        if trace is not None and isinstance(trace, bound.ExecutionTrace):
            ask_params["execution_trace"] = trace

        return self.ask(
            message, model_key, conversation_id, ask_params, execution_trace=trace
        )
