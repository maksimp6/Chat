# Обзор API

Этот документ — индекс текущих HTTP API Alice Pro. Он не заменяет route-level
контракты и тесты: фактический source of truth для существования endpoint —
зарегистрированные Flask routes и их regression tests.

## Основные поверхности

| Surface | Prefix / endpoints | Current implementation |
| --- | --- | --- |
| Chat / tools | `/api/chat`, `/api/skills`, MCP approval/tool routes | `mcp_routes.py` |
| Users / auth | `/api/users/bootstrap`, `/api/auth/me`, GitHub OAuth routes | `app.py`, `identity/github_oauth.py` |
| Conversations / memory | conversation/history/memory routes under `/api` | `app.py` and storage modules |
| Files | `/api/files` | `file_routes.py` |
| Runtime / sessions | `/api/session-profiles`, session/invocation runtime routes | `runtime_api.py` |
| Branch environments | `/api/environments`, `/environments/<environment_id>/...` gateway | `environment_routes.py`, `environment_manager.py` |
| Plugins | `/api/plugins` | `plugin_routes.py` |
| Departments | `/api/departments` | `departments.py` |
| Government workflows | `/api/government` | `government.py` |
| Voice | `/api/voice/*` | `voice_routes.py` |
| Local agents | `/api/local-agents` | `local_agent_gateway.py` |
| Project tree | `/api/project-tree` | `project_tree.py` |
| Cloud.ru IAM | `/api/cloudru/iam` | `cloudru_iam_routes.py` |
| 3D Printing | `/api/3d` | `printing3d/routes.py` |
| ChatGPT MCP transport | MCP resource/transport routes | `mcp_server/transport.py` |

## Focused references

- [3D Printing HTTP API](printing3d.md) — quote, owner-scoped orders,
  settlement, business P&L, finance plan и payback status.
- [Reusable AI sessions](../runtime_serverless.md) — built-in session profiles
  and clone/use lifecycle.
- [Branch-aware environments](../branch-environments.md) — local immutable
  branch/commit environment API and lifecycle.
- [Voice assistant](../voice-assistant.md) — voice session/audio/events/output
  contract.
- [Plugin system](../plugins.md) — plugin discovery/configuration lifecycle.
- [ChatGPT Apps SDK / MCP](../mcp/chatgpt_apps.md) — external ChatGPT/MCP
  integration boundary.

## Evidence boundary

Наличие route и deterministic regression test означает, что repository contract
реализован. Это не доказывает доступность endpoint на production deployment,
корректность внешнего OAuth/provider integration или успешный live request.
Такие утверждения требуют проверки exact deployed revision в соответствующем
runbook/acceptance evidence.

## Maintenance rule

При добавлении новой публичной API surface обновляйте этот индекс или создавайте
focused reference и добавляйте ссылку сюда. Не копируйте большие request/response
schemas вручную, если они уже определены named contract/schema в коде: generated
API/config reference — отдельный ratchet #978.
