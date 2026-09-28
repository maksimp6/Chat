# Канонический pipeline выполнения ИИ

Issue: #433

Alice Pro использует один канонический путь выполнения запросов модели и инструментов.
Транспорт или интерфейс не должен создавать собственный независимый agent loop.

## Канонический путь

```text
request / task
  ↓
InvocationContext + persisted invocation
  ↓
provider adapter
  ↓
Responses API / protocol tool loop
  ↓
UniversalToolExecutor + ToolRegistry
  ↓
scope / policy / approval
  ↓
Execution Trace + billing
  ↓
response
```

## Канонические компоненты

- `invocation/` — идентичность и жизненный цикл одного запуска;
- `yandex_client.py` и `yandex_client_modules/` — текущий primary provider adapter;
- `responses_tool_loop.py` — protocol helper для Responses API;
- `universal_tool_platform.py` — единый контракт и pipeline выполнения инструментов;
- `tool_registry.py` — единый каталог инструментов;
- `trace_manager.py` и связанные trace-модули — наблюдаемость;
- treasury/billing слой — учёт стоимости и лимитов.

## Адаптеры

Адаптеры могут принимать запросы и переводить их в канонический runtime, но не должны
реализовывать собственный цикл "модель → инструменты → модель".

К адаптерам относятся:

- веб-чат / Flask;
- MCP / ChatGPT;
- browser;
- Android / local tool agent;
- GitHub issue agent;
- CLI/Termux entrypoint (`cli_agent.py`);
- A2A / agent gateway.

## Удалённый legacy

Следующие экспериментальные loops удалены как неиспользуемые production runtime:

- `run_agent.py`;
- `run_agent_loop.py`;
- `yandex_agent_loop.py`;
- `agent_runner.py`.

Они напрямую вызывали старые completion/OpenAI-compatible endpoints и имели собственные
tool protocols, поэтому обходили единые invocation, approval, trace и billing contracts.

## Уже переведено на canonical runtime

- `cli_agent.py` — тонкий CLI/Termux-адаптер к `/api/conversations`, `/api/chat` и стандартному approval endpoint; прямых Yandex API calls и локального shell execution больше нет.
- `alice_agent_runner.py` — GitHub issue adapter создаёт и запускает invocation, сохраняет финальный Execution Trace и завершает invocation как completed/failed.

## На апгрейд

- `local_tool_agent.py` — живой Android/local transport adapter;
- `agent_gateway.py` — provider-neutral/A2A routing;
- `local_agent_gateway.py` — HTTP transport/runtime gateway, имя и границы нужно уточнить.

## Правило

Новый production entrypoint не может напрямую создавать альтернативный model/tool loop.
Если нужен новый интерфейс, он подключается адаптером к каноническому pipeline.
