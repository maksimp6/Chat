# Обзор API

Alice Pro использует Flask HTTP API вместе с отдельной MCP Streamable HTTP поверхностью. Этот документ — карта границ, а не полный автоматически сгенерированный route catalog.

## Основные поверхности

- `GET /healthz` — минимальная liveness-проверка Flask application; возвращает безопасный статус без DB/secret metadata.
- `POST /api/chat` — основной backend chat path, зарегистрированный в `mcp_routes.py`; model/tool lifecycle проходит через существующие invocation/trace boundaries.
- `/mcp` — отдельная MCP поверхность для совместимых внешних клиентов; контракт и authentication описаны в [MCP docs](../mcp/chatgpt_apps.md).
- Дополнительные product/API routes принадлежат своим модулям и документам.

Starter OpenAPI для Cloud.ru API Gateway намеренно не является полным Flask route map и не считается опубликованным gateway contract без отдельной live-проверки.

## Authentication

Authentication определяется конкретной поверхностью и deployment mode. `/healthz` может оставаться публичным для liveness; protected application/MCP routes не становятся публичными из-за наличия gateway или документации.

Никогда не передавайте provider keys, secret values или bearer credentials как обычные model/tool payloads.

## Execution evidence

HTTP 200 отдельного route не доказывает полный пользовательский сценарий. Production acceptance должно проверять требуемую цепочку auth → API/MCP → application behavior на exact deployed revision.

## Прикладные API

- [3D Printing HTTP API](printing3d.md) — quote, owner-scoped orders, settlement, business P&L, finance plan и payback status.
- [3D Printing Business](../printing3d.md) — сводная продуктовая граница.
