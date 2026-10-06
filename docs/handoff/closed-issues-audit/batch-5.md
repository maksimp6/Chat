# Аудит CLOSED Issues maksimp6/Chat: партия 5

47 Issues, закрытых 2026-10-04 как duplicate или not planned, на которые ссылается хотя бы один открытый Issue. Master `dfda42c`.

Партия больше обычной, потому что проверка однотипная. Для каждого Issue я нашёл в открытом каноническом Issue запись «Issue compaction: absorb #N» (или аналог) и проверил, что оставшийся scope там прямо перечислен. Ссылки из дайджеста Observer #810 не засчитывал: это журнал событий, а не владелец.

| Issue | исходная цель | почему закрыт | evidence в каноническом Issue / master | verdict |
|---|---|---|---|---|
| #470 | Docs Work Map / trips.db | duplicate, перенесён в #551 | #551 закрыт PR #833, который только убрал #340; PR #471 не смержен; в `docs/` нет ни слова о Work Map | **reopened** |
| #797 | Старый дайджест Observer | not planned (stale) | #592: stale-дайджест закрыт, Observer создал свежий #810 | verified closed |
| #770 | Разгрузка Alice через CI/дешёвые модели | duplicate | #576: «absorb #770 as Coordinator execution-routing/economy policy» | covered by #576 |
| #766 | Homa для внутреннего MCP-транспорта | not planned | решение владельца «revisit later»; #783: «remains independent until measurements justify adoption» | verified closed |
| #715 | Сообщения пользователя в аудиорежиме | duplicate | #714: «absorb #715 as the voice-input surface» | covered by #714 |
| #712 | Защищённый merge для Maintainer | duplicate | #496: «absorbs #525, #626, #633 and #712» | covered by #496 |
| #707 | Yandex Skills API | duplicate | #433 (исполнение) и #254 (версии/безопасность) | covered by #433 |
| #705 | Бесплатный API live-счёта Dota 2 | duplicate | #706: «Former live-source issue #705 is absorbed here» | covered by #706 |
| #702 | Sync/async режимы исполнения | duplicate | #576: «absorbs #580, #629, #634, #636, #697, #701 and #702» | covered by #576 |
| #701 | Авто-продолжение Coordinator от GitHub-событий | duplicate | то же, #576 | covered by #576 |
| #700 | Запрет дублей Issues | duplicate | #576: «absorb #700 into Coordinator issue-planning policy» | covered by #576 |
| #697 | Идемпотентные handoff Maintainer | duplicate | #576 (список absorb) | covered by #576 |
| #685 | Противоречия role/skill и контракт/тест | duplicate | #604: «absorb #685 into the canonical contract-first lifecycle» | covered by #604 |
| #683 | Удалённое управление rooted Android | duplicate | #650: «Supersedes #648 and absorbs #683» | covered by #650 |
| #682 | Бонусная работа TODO/FIXME | not planned | #576 берёт только политику мелких улучшений; gamification отклонена явно | covered by #576 |
| #668 | Изучение внешних GitHub-проектов | duplicate | #576: «research-before-build policy» | covered by #576 |
| #667 | Находки аудита PR #651 | not planned | разнесены по владельцам: F06-F12 в #650, F04/D01 в #576, F01-F03/F05 в #496 | covered by #496 / #576 / #650 |
| #663 | Ускорение разработки, мнения агентов в Issues | duplicate | #576: «absorb #663 into the shared coordination/process surface» | covered by #576 |
| #662 | Учёт токенов/кеша/биллинга сессий агентов | duplicate | #767: «authoritative workforce usage/accounting telemetry contract» | covered by #767 |
| #661 | Planner/supervisor для браузера | duplicate | #409: «absorb #661 as the model-backed planning/supervision layer» | covered by #409 |
| #648 | Свой Termux с root-агентом | not planned | #650: «#648 is superseded by this direct Alice Pro integration» | covered by #650 |
| #645 | Нативное Linux-приложение, Wine | duplicate | #643: «absorb #645 into this canonical cross-platform desktop-client issue» | covered by #643 |
| #636 | TaskPacket, single-active-task | duplicate | #576 (список absorb) | covered by #576 |
| #634 | RED-контракт TaskPacket | duplicate | #576 (список absorb) | covered by #576 |
| #633 | Проверка evidence task-state | duplicate | #604: «their hardening is **not** complete on current master»; ещё #496 | covered by #604 |
| #629 | Краткое мнение role-агентов | duplicate | #576 (список absorb) | covered by #576 |
| #626 | Право на solution-review | duplicate | #604 (то же явное «not complete»), ещё #496 | covered by #604 |
| #618 | Инструменты и бюджет Claude Direct | duplicate | #576: capability preflight; сам backend Claude удалён в #844 | covered by #576 |
| #602 | Heartbeat Observer без cron | duplicate | #576: «event-driven Observer wakeups (#602)» | covered by #576 |
| #598 | 30-дневный error budget | duplicate | #592: «#598 tracks real rolling 30-day evidence» | covered by #592 |
| #594 | Fault-injection matrix | duplicate | #592 (PR #596); блокер #591 уже смержен | covered by #592 |
| #581 | Дашборд прогресса и usage | duplicate | #537: «absorb #581 as the compact progress/usage projection» | covered by #537 |
| #580 | Общий Work Coordinator | duplicate | #576 (список absorb) | covered by #576 |
| #531 | Полный технический аудит | duplicate | #538 (переоткрыт в партии 1): «absorb #531 as the ongoing deterministic technical-audit surface» | covered by #538 |
| #481 | Триаж Discussions | duplicate | #576: Issues-first, Discussions только как недоверенный ввод | covered by #576 |
| #478 | JSON stdout для Container Apps | duplicate | #783: «absorb #478 into Alice Platform observability» | covered by #783 |
| #475 | Новые сервисы Cloud.ru | not planned | #783: «absorb the broad Cloud.ru service-inventory» | covered by #783 |
| #473 | ChatGPT UI для Execution Trace | duplicate | #537: «optional external rendering adapter» | covered by #537 |
| #446 | A2A-транспорт к Cloud.ru AI Agents | duplicate | #767 (identity/invocation), #783 (адаптер) | covered by #767 |
| #445 | Песочницы через Cloud.ru API | duplicate | #783: «absorb #444 and #445 into the platform runtime contract» | covered by #783 |
| #444 | Фоновые задачи вне веб-процесса | duplicate | то же, #783 | covered by #783 |
| #440 | Эпик Cloud.ru-native платформы | not planned | #783: «#440 is superseded» | covered by #783 |
| #350 | Архитектура инфраструктуры и runtime | not planned | #783: «superseded … by this platform desired-state contract plus #776 and #576» | covered by #783 |
| #256 | Плагин автономной разработки | duplicate | #576 + #604 + #496 | covered by #576 |
| #238 | MCP как интерфейс чтения/управления | duplicate | #326: «absorb #238 into this canonical external MCP connectivity/control contract» | covered by #326 |
| #223 | Минимальная версия ≤50 КБ | duplicate | #227: «absorb #223 … measured critical-resource budget» | covered by #227 |
| #56 | Настройка репозитория в вебе | not planned | #496: «absorb the repository-protection portion of #56 and #423» | covered by #496 |

## Находка

Перенос в Issue, который потом закрыли, теряет перенесённую работу. #470 перенесли в #551. #551 закрыл узкий PR #833, а раздел про Work Map так и не сделали. Остальные «absorb»-переносы этой партии указывают на Issues, которые до сих пор открыты, так что там scope не потерян.

## Документация (для потока docs)

- В docs нет страницы Work Map/trips.db, это и есть предмет переоткрытого #470. Правку docs здесь не делал.
