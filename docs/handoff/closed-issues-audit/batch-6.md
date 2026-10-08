# Аудит CLOSED Issues maksimp6/Chat: партия 6

Issues с #710 по #230, кроме уже проверенных: закрытые 2026-09-23…2026-10-04, без ссылки из открытых Issues. Master `dfda42c`.

Ещё проверил, куда ведут все записи «absorb / supersede» на Issues, которые потом закрыли. Работу потеряли только #470 (партия 5) и #569 (эта партия). В остальных случаях это цепочки решений «не делать».

| Issue | исходная цель | почему закрыт | evidence в master | verdict |
|---|---|---|---|---|
| #710 | Допуск работы агентов: pre-start и closeout | PR #711 | `docs/agents/role-based-agent-office.md` (pre-start/closeout) | verified closed |
| #690 | Termux: не отдавать детали исключений | duplicate → #693 → #695 | дефект на месте, конкретику я уже дописал в #695 | covered by #695 |
| #689 | Android cryptography | duplicate → #694 → #695 | `android/requirements.txt:9`, в #695 | covered by #695 |
| #688 | File routes: realpath | duplicate → #692 → #695 | `file_routes.py:213-234`, в #695 | covered by #695 |
| #673–#680 | Зеркало private/public бизнес-документов (8 Issues) | duplicate → #671 | #671 not planned по решению владельца (партия 1) | verified closed |
| #664 | Bounded runner tooling, Discussions | not planned | решение владельца: исходная причина снята, Discussions заменены Issues | verified closed |
| #640 | Прогресс-комментарии Claude блокируют параллельную работу | PR #641 | смержено; backend Claude позже удалён в #844 | verified closed |
| #638 | Бюджет ходов Claude Direct | PR #639 | смержено; устарело после #844 | verified closed |
| #625 | Gate solution-review (дубль пути) | not planned | реализация в PR #624 (смержен) | verified closed |
| #610 | Роутер ролевых alias | not planned | вытеснено моделью role/stage/backend из #616 (`agent_office/dispatch_model.py`) | verified closed |
| #608 | Стабильные `@agent-*` alias | not planned | то же: #616 «Supersedes … #608» | verified closed |
| #606 | Observer понимает принятый RED-контракт | not planned; PR Codex не смержен | в master нет `expected_red_contract`; #604 прямо содержит это требование | covered by #604 |
| #583 | Handoff Maintainer: завершить или эскалировать | PR #589 | смержено, `agent_office/observer.py` | verified closed |
| #572 | Семантический DAG браузера | PR #659 | `browser/pipeline.py`, `tests/test_browser_pipeline.py` | verified closed |
| #569 | Docs: развести runtime-агентов и роли разработки | duplicate, scope ушёл в #551 | на `role-based-agent-office.md` ссылается только `AGENTS.md`; `docs/README.md` и `docs/agents/overview.md` не изменены; #551 закрыт узким PR #833 | **reopened** (пункт про `@claude-lite` помечен устаревшим) |
| #565 | Параллельная ролевая DAG-оркестрация | PR #567 | `browser/orchestration.py`, `tests/test_browser_orchestration.py` | verified closed |
| #561 | Docs 3D-бизнеса и web search | PR #563 | `docs/printing3d.md` | verified closed |
| #519 | Runbook Container Apps v2 | duplicate → #514 | сделано в PR #520 | verified closed |
| #486 | Ускорить Python/PG пайплайн | not planned | цель достигнута без xdist (24,5 с против 130 с); дальнейшая латентность в #761 | verified closed |
| #454 | Разбить `chatgpt_mcp.py` | PR #457 | `mcp_server/{auth,protocol,transport,tools,runtime_bridge}.py`, `tests/test_mcp_server_modules.py` | verified closed |
| #438 | Удалить Supabase | PR #439 | `supabase` в коде и workflow не встречается | verified closed |
| #428 | Markdown-зеркало документации Cloud.ru | not planned (владелец, 10-05); crawler из PR #448 не смержен | crawler-а в master нет | verified closed (drift в docs, см. ниже) |
| #417 | Коннектор провайдера Cloud.ru | PR #418 | `cloudru_iam.py`, `static/provider_credentials.js` | verified closed |
| #401 | Детерминированные snapshot/finalize trace | PR #402 | смержено | verified closed |
| #391 | «wow it yurself» (заметка о безопасности PR) | PR #396 | DoD нет; PR восстановил аудит trace локального runtime | verified closed |
| #389 | Локальный инструмент выполнения команд | PR #390 | `runtime_tools.py`, `tests/test_local_runtime_exec.py` | verified closed |
| #388 | Точка входа для донатов | PR #394 | `ALICE_DONATION_URL` в `config.py`, `tests/test_donation_entry_point.py` | verified closed |
| #379 | SSH-ключ для Codex | not planned, без комментария | SSH-деплой выведен (#869) | verified closed (устарел) |
| #351 | Интеграционная ветка Codex | not planned | решение владельца: ветка ahead=0, процесс заменён | verified closed |
| #343 | Umbrella Codex по архитектуре | not planned | решение владельца; остаток в #576/#783 | verified closed |
| #334 | Детектор проглоченных ошибок и покрытие | PR #335 | `tests/test_error_observability.py` | verified closed |
| #294 | Настройки SSH Runtime | PR #304 | `ssh_runtime_settings.py`, `static/settings/ssh_runtime_modal.js` | verified closed |
| #289, #277, #275, #273, #271, #268, #267, #265 | Баги preview/Dozzle и ключей провайдера | PR #292, #278, #276, #274, #272, #270, #269, #266 (все смержены, «Closes») | точечные исправления своего времени; `static/dozzle.js` на месте | verified closed |
| #255 | Динамический список моделей | PR #261 | `model_discovery.py` | verified closed |
| #253, #252, #251, #249, #242, #240, #239 | UI/API-баги | PR #260, #258, #258, #259, #263, #241, #264 (смержены) | точечные исправления | verified closed |
| #232, #231, #230 | GitLab Duo plugin | not planned | решение владельца: control plane теперь Alice, остаток в #326/#576 | verified closed |

## Документация (для потока docs)

- `docs/integrations/cloudru-docs-mirror.md` пишет «Статус: planned, Issue: #428», хотя #428 закрыт как not planned и crawler-а нет. `docs/mirrors/README.md` и `docs/notes/cloudru/README.md` описывают несуществующий pipeline.
- Навигация по `role-based-agent-office.md` отсутствует. Это предмет переоткрытого #569.
