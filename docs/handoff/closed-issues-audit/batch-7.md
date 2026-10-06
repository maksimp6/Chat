# Аудит CLOSED Issues maksimp6/Chat: партия 7 (последняя)

Самые ранние Issues, #4–#287, закрытые 2026-09-15…2026-09-25. Почти все закрыты смерженными PR со словом «Closes». Это период до перехода на Cloud.ru/RDC и FileMemoryDB, поэтому для части Issues оценка звучит как «верно для своего времени». Master `dfda42c`.

| Issue | исходная цель | почему закрыт | evidence в master | verdict |
|---|---|---|---|---|
| #287 | Восстановить Execution Trace Viewer | PR #288 | `static/trace_viewer.js` | verified closed |
| #220 | Явная публикация портов VPS | PR #221 | VPS-путь выведен (#869) | verified closed (для своего времени) |
| #219 | Загрузка сайта, Eruda, кнопки | PR #222 | точечный фикс | verified closed |
| #204 | Проверить VPS preview | duplicate → #205 | #205 verified (партия 4) | verified closed |
| #200 | Treasury: 401 | PR #201 + коммит `b830a6d` «Closes #200» | фикс в PR, авто-закрытие совпало с реальным фиксом | verified closed |
| #199 | Preview для любой ветки/PR | PR #203 | `preview-deploy.yml` | verified closed |
| #198 | Заголовки диалогов | PR #297 | `conversation_metadata.py` | verified closed |
| #196 | Ошибка owner_id | PR #197 | `treasury_identity.py`, `user_identity.py` | verified closed |
| #148, #147 | Favicon, локальные ассеты, Eruda | PR #150; #147 — дубль | точечный фикс | verified closed |
| #145 | owner_id в биллинге, Android | PR #146 | точечный фикс | verified closed |
| #132, #120, #107 | Перекрытие UI системными элементами Android | PR #138, #123, #108 | WindowInsets-обработка (#120: подробная фиксация) | verified closed |
| #128 | «feature add good title» | not planned | нет DoD; комментарий владельца — рассуждение | verified closed |
| #127 | Universal Tool Platform | PR #151 | `universal_tool_platform.py` (approval-границы), используется в `yandex_client_modules/mcp_mixin.py:50`, `local_tool_agent.py:154`, `runtime/dispatcher.py`; тесты `test_universal_tool_*`. Чекбоксы в теле Issue не обновлены | verified closed |
| #119 | Логирование и диагностика Android | PR #121 | подробная фиксация в комментарии | verified closed |
| #118 | Спринтовый регламент | вручную | регламент зафиксирован; процесс позже заменён #576/#604 | verified closed |
| #113 | Цветовые схемы и AI-ассистент темы | PR #298 | `tool_providers/theme.py` | verified closed |
| #111 | Автообновление Android | PR #112 | `UpdateManager.kt` | verified closed |
| #109 | Native Agent Gateway | PR #171 | `agent_gateway.py` | verified closed |
| #106 | Авторегистрация при первом запуске | PR #185 | `user_identity.py` | verified closed |
| #102 | Нативное Android-приложение | PR #103 + коммит `bd299c8` «Closes #102» | `android/` | verified closed |
| #94–#97 | Случайные placeholder-ы | not planned | пусто | verified closed |
| #93, #91, #89, #87, #61 | Supabase: миграции, зеркало trace | PR #98, #92, #90, #88, #62/#86 | Supabase удалён целиком в #438 | verified closed (устарел) |
| #85 | Применять изменения БД после merge в prod | PR #157 | SSH/Supabase-путь выведен; БД — #776, lifecycle — #869 | verified closed (устарел) |
| #83 | Кнопки «Расходы»/«Пополнение» | duplicate → #12 | `static/treasury.js:118,142` (партия 3) | verified closed |
| #82, #80, #77, #74, #70, #68 | Логи, иконка скачивания trace, rollback docs, `api_logger`, разбиение `yandex_client.py`, чистка логов | PR #153, #152, #78, #79, #71, #69 | `yandex_client_modules/` и т.д. | verified closed |
| #63 | Рефакторинг трейсинга | PR #189 | `trace_timing.py`; тело Issue фиксирует, что Viewer оставлен намеренно | verified closed |
| #59 | Local Tool: git_log | PR #67 | function_call_output continuation | verified closed |
| #58 | Связи между событиями trace | PR #163 | смержено | verified closed |
| #54, #53, #51 | Ключи провайдеров на пользователя и их ротация | not planned | решение владельца; модель с одним владельцем (#196), квоты сделаны в #52 | verified closed |
| #52 | Квоты и rate limit на пользователя | PR #299 | `provider_quotas.py`, `provider_quota_routes.py`, `tests/test_provider_quotas.py` | verified closed |
| #50 | Глобальный ключ Yandex с ротацией 12 ч | PR #73 | `yandex_api_key_provider.py`; хранение секретов переходит в Secret Store (#755) | verified closed |
| #47, #45, #40, #36, #34 | Валидация метаданных, тесты сессий, InvocationContext, `.env`, секреты из кода | PR #72, #158, #46, #37, #35 | `invocation/` и т.д. | verified closed |
| #39, #38 | Лимит результатов поиска файлов | not planned | решение владельца | verified closed |
| #28, #26, #22, #18 | Prompt caching, аудит trace, разрыв 450 мс, polling responses | PR #29, #27, #23, #19 | `cached_tokens` в парсерах; #18 перепроверен владельцем на master | verified closed |
| #20 | «Убрать этот лимит» | PR #186 | смержено | verified closed |
| #16, #15, #14 | Учёт стоимости знаний, биллинг в trace, trace ↔ InvocationContext | PR #32, #30, #31 | `knowledge_economics.py`, `billing.py`; добавка про Finance Agent из комментария #14 — теперь учёт workforce в #767 | verified closed |
| #13 | Окружения по веткам | not planned | решение владельца: вытеснено архитектурой #350 → #783 | verified closed |
| #11 | Partner Relations Department | PR #301 | `partner_relations.py` | verified closed |
| #9 | Departments | PR #183 | `departments.py` | verified closed |
| #5 | Готовые AI-сессии / desired state инфраструктуры | not planned | в master только профили сессий (`session_profiles.py`, PR #323); acceptance про desired state ведёт #783 | covered by #783 (дописал ссылку) |
| #4 | Serverless + sessioned исполнение | PR #114 | `environment_manager.py`; дальше #350 → #783 | verified closed |

## Итог по ранним Issues

Переоткрытий нет. Два авто-закрытия коммитами из партии 1 (`Closes #200`, `Closes #102`) оказались безвредными: оба Issues закрыты и настоящими PR-фиксами.
