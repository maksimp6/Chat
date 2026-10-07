# Структура бэкенда

Alice Pro использует Flask как основной HTTP application entrypoint, но backend не является одним монолитным слоем хранения или выполнения.

## Основные границы

- `app.py` — Flask application entrypoint и сборка web runtime.
- API/routes — HTTP boundary для UI и интеграций.
- Provider/model path — Yandex Responses API и связанные adapters.
- Universal Tool Executor / MCP/local tools — единая граница выполнения инструментов.
- InvocationContext / ExecutionTrace — correlation, timing, errors, tool/provider lifecycle и billing evidence.
- RuntimeDispatcher — owner/runtime-scoped dispatch boundary.
- Storage — переходный слой: legacy SQL consumers сосуществуют с file-native Memory DB consumers до завершения #776.

## Данные

Backend-код не должен напрямую разбирать journal/checkpoint FileMemoryDB. Доступ к file-native state идёт через typed repositories/stores. Legacy SQL остаётся только там, где consumer ещё не мигрирован.

Secret values принадлежат canonical Secret Store boundary #755 и не должны попадать в Memory DB, SQL, prompts, ordinary tool results, logs или ExecutionTrace.

## Production evidence

Наличие route, adapter или теста в master не доказывает, что соответствующая внешняя поверхность работает в production. Для Cloud.ru, MCP/ChatGPT, OAuth и browser acceptance используются отдельные live gates/issues.

## Связанные документы

- [Architecture overview](../architecture/overview.md)
- [Memory](../memory/overview.md)
- [MCP](../mcp/overview.md)
- [Execution trace lifecycle](../execution-trace-lifecycle.md)
- [Platform architecture](../platform/architecture.md)
