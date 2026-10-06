# Аудит CLOSED Issues maksimp6/Chat: партия 4

26 Issues, закрытых как completed вручную, без merge PR в момент закрытия. Master `dfda42c`. Для каждого искал PR, на который ссылается закрывающий комментарий, и сверял его с master. Где можно, запускал тесты.

| Issue | исходная цель | почему закрыт | evidence в master | verdict |
|---|---|---|---|---|
| #627 | Реализовать state machine задач агентов | вручную; PR #628 закрыт без merge | `agent_office/task_state.py` `derive_agent_task_state`, 11 состояний; `tests/test_agent_task_state.py` проходит (42 passed вместе с dispatch) | verified closed |
| #614 | RED-контракт state machine | вручную; PR #615 закрыт без merge | контракт `tests/test_agent_task_state.py` в master; подключение к runtime ведёт #604 («blocked/stalled/cancelled stop forward transitions») | verified closed |
| #616 | Роль ≠ стадия ≠ backend | вручную, запуск бота упал; PR #617 закрыт | `agent_office/dispatch_model.py:1,8,17,116-128` (role/stage/backend), `tests/test_agent_dispatch_model.py` | verified closed |
| #623 | Gate solution-review | вручную | PR #624 смержен 2026-09-30; `tests/test_agent_dispatch_model_solution_review.py` | verified closed |
| #621 | Независимый review качества после GREEN | вручную | тот же PR #624 | verified closed |
| #611 | Claude Lite: реальный запуск и evidence | вручную, evidence из PR #696 | разделение intent/trigger/dispatch в `agent_office/observer.py`; сам backend Claude удалён в #844 | verified closed |
| #397 | Claude как исполнитель, не ревьюер | вручную, запуск бота упал | цель устарела: backend Claude удалён в #844 | verified closed |
| #541 | Docs developer tooling | PR #547 | hot-file metrics, AST index, hook contract в docs | verified closed |
| #539 | Единый pre-commit hook | PR #542 | `scripts/install_git_hooks.sh`, вызывает `format.sh check` | verified closed |
| #524 | Docs merge-readiness CLI | PR #529 | раздел Read-only merge readiness | verified closed |
| #522 | Merge readiness как status check | PR #523 | `merge-readiness.yml`; разрыв fail-closed статуса Format уже в #496 (партия 1) | verified closed |
| #514 | Синхронизация README/changelog | PR #520 | разовая сверка; регулярную ведёт поток docs | verified closed |
| #500 | Async/concurrency matrix | PR #528 | `tests/test_async_runtime_concurrency.py` | verified closed |
| #490 | TTL plaintext-кеша секретов | PR #493 | `secret_management.py:121-142` кеш не держит plaintext | verified closed |
| #487 | Единый formatter toolchain | PR #488 | `scripts/format.sh write\|check`, pinned Ruff/Prettier | verified closed |
| #332 | Observability загрузки моделей | PR #338 | lifecycle-диагностика и тесты frontend | verified closed |
| #285 | Пересмотр PostgreSQL как основной БД | переоценка в комментарии | решение устарело: хранилище переходит на FileMemoryDB | covered by #776 |
| #235 | HTTPS домена и /short-token/ | проверка на VPS | VPS/SSH-путь выведен (#869); short-token сейчас в работе отдельно (#868) | verified closed (для своего времени) |
| #205 | E2E проверка VPS preview | успешные прогоны | то же, VPS-путь выведен в пользу RDC (#869) | verified closed (для своего времени) |
| #125 | Отдельная кнопка обновлений Android | реализовано, без комментария | `UpdateManager.kt`, `update-app-btn` в `templates/index.html`, `tests/test_header_actions_asset.py` | verified closed |
| #75 | Удобная работа с кодом с Android | инкремент #119/#120 | PR #121, #123 смержены | verified closed |
| #60 | Supabase RLS `rls_auto_enable()` | без комментария | Supabase в master нет совсем (#438) | verified closed (устарел) |
| #57 | Настроить GitHub Agents | ревизия | `AGENTS.md`, `dispatch_model.py`, `.agents/skills/*` | verified closed |
| #55 | Сделать репозиторий публичным | без комментария | репозиторий публичный (API отдаёт данные без авторизации) | verified closed |
| #8 | REAL/DEMO бюджет Gambling Department | комментарий «- [x] Read» | `budget_controller.py`: REAL/DEMO, `DemoConversionDenied`, fallback, `max_single_operation`, trace_sink; `tests/test_budget_controller.py` 15 passed | verified closed |
| #7 | Prompt caching Yandex в ExecutionTrace | без комментария | `cached_tokens` в `yandex_response_parser.py:58`, `billing.py`, `config.py:207`, `tests/test_yandex_client_trace.py` | verified closed |

## Итог партии

Ни одного переоткрытия. Все ручные закрытия подтверждены кодом или явным решением. Подозрения из прошлой сводки не подтвердились: #8 закрыт с неинформативным комментарием, но REAL/DEMO-контроллер в master есть и тесты проходят. State machine (#614/#627) тоже в master, хотя её PR закрыты без merge: код пришёл другим путём.

Замечание: `task_state.py` пока используется только в тестах, runtime его не вызывает. Это не долг #614 (там была цель «определить»), а часть lifecycle-работы #604.
