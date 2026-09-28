# Агенты и адаптеры Alice Pro

В проекте есть три разных понятия: пользовательские transport adapters,
runtime-компоненты для маршрутизации агентов и GitHub-агенты разработки. Они не
образуют общую очередь в SQLite и не координируются через лог-файлы.

## Пользовательские адаптеры

Все production entrypoints должны использовать единый backend/provider/tool
pipeline и сохранять correlation через `InvocationContext` и `ExecutionTrace`.

| Адаптер | Реализация | Текущая граница |
| --- | --- | --- |
| Web/Android chat | `app.py`, `static/chat.js`, Android WebView | Вызывает backend chat API; model/tool loop находится на сервере. |
| CLI/Termux | `cli_agent.py` | Вызывает `/api/conversations`, `/api/chat` и стандартный approval endpoint; не хранит provider key и не выполняет локальный shell. |
| Voice | `voice_routes.py`, `static/voice.js` | SpeechKit STT/TTS + общий chat client; сессии хранятся в памяти процесса и имеют TTL. |
| MCP/ChatGPT | `mcp_routes.py`, `chatgpt_mcp.py` | Представляет нормализованные tools через MCP transport; выполнение остаётся за общим executor/policy. |
| GitHub issue | `alice_agent_runner.py`, `.github/workflows/alice.yml` | Ограниченный filesystem-only запуск Alice, draft PR и финальный trace. |

Канонический путь и список удалённых legacy loops описаны в
[AI execution pipeline](../architecture/ai-execution-pipeline.md).

## Runtime agent foundation

- `agent_gateway.py` задаёт provider-neutral registry, permissions, retries,
  rate limits и circuit breaker для local/REST/A2A handlers.
- `runtime/conversation_agents.py` связывает агента с конкретными
  `runtime_id` и `conversation_id`; tool call проходит только через
  `RuntimeDispatcher`.
- `local_agent_gateway.py` — локальный HTTP transport gateway.

Это foundation, а не подтверждение всех внешних интеграций. Cloud.ru A2A
transport находится в открытом [PR #456](https://github.com/maksimp6/Chat/pull/456)
и до merge/проверки не считается доступным в `master`.

## GitHub-агенты разработки

Роли и merge-policy определены только в корневом `AGENTS.md`:

- Alice делает небольшие изменения через sandboxed filesystem tools;
- Claude сопровождает и сливает готовые PR;
- Codex проверяет тесты и берёт сфокусированные test-heavy задачи;
- Copilot выполняет назначенные задачи и review.

Эти роли работают через issues, pull requests, CI и review. Они не являются
внутренними чат-агентами пользовательского runtime.

## Наблюдатель за агентами

`agent_office/observer.py` по расписанию читает GitHub timeline, checks и review,
строит одну хронологию на задачу и обновляет tracking issue. Он:

- обнаруживает red CI, конфликты, отсутствующее review и длительное бездействие;
- загружает JSON threads как workflow artifact;
- не комментирует PR, не упоминает агентов, не пушит и не выполняет merge.

Подробности: [Agent observer](agent-observer.md).

## Наблюдаемость

`ExecutionTrace` — основной аудит одного invocation: provider requests, tool
calls, approvals, события, timing, ошибки и billing. Ротируемые файлы из
`logger.py` (`logs/app.txt`, `logs/api_debug.txt`, `logs/voice.txt` и другие)
нужны для диагностики процесса, но не являются шиной сообщений или источником
состояния агентов.

Финальный trace сохраняется явно вызывающим кодом. Снимок trace — read-only
операция; правила lifecycle описаны в
[ExecutionTrace snapshot lifecycle](../execution-trace-lifecycle.md).
