# Cloud.ru + второй Android-телефон

## Cloud.ru

Разместите Alice Pro как Flask-приложение за HTTPS reverse proxy.

Минимальные переменные:

\`\`\`env
HOST=0.0.0.0
PORT=8080
ALICE_MCP_PUBLIC_URL=https://<public-host>
ALICE_LOCAL_AGENT_BOOTSTRAP_TOKEN=<long-random-secret>
\`\`\`

Для MCP production используйте внешний OAuth resource-server, как описано в \`docs/mcp/chatgpt_apps.md\`.

## Телефон

При первом запуске нового Android-приложения выберите **Local agent**, затем укажите URL Cloud.ru, bootstrap token и Agent ID.

В этом режиме приложение не поднимает публичный Flask-порт. Оно устанавливает исходящие HTTPS-соединения с Cloud.ru и периодически забирает задания.

Домашний роутер не требует проброса порта. WireGuard/Tailscale может использоваться позже как дополнительный транспорт, но для базового pull-протокола он не обязателен.

## Проверка

Серверный diagnostic endpoint:

\`\`\`text
GET /api/local-agents/health
Authorization: Bearer <bootstrap-token>
\`\`\`

Endpoint не выдаёт runtime tokens и требует bootstrap token.
