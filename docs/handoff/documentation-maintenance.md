# Сверка документации с master

Состояние на 2026-10-06, master `d1c235b` (исходный снимок `d365c05`).

## Базовый аудит (завершён)

Аудит дрейфа документации прошёл по master `bd120d4`…`943bf8e` и охватил Memory DB и backup/restore, Secret Store, Chrome/RDC deployment, OAuth, инфраструктуру Cloud.ru и CI.

Найдены и исправлены в PR #867 (смержен):

1. `docs/memory/overview.md` утверждал, что память хранится in-memory. На деле хранение SQL-backed (`memory_manager.py`, `memory_extractor.py`). Документ исправлен, упомянута реализация FileMemoryDB (#856, #857) и план миграции #776 (Wave 1).
2. `deploy/chrome-worker/oauth.mjs` реализует scope `offline_access` (refresh token 30 дней, access token 15 минут, вход через GitHub, регистрация клиентов с вытеснением через 7 дней), а документации не было. Добавлен раздел OAuth в `docs/architecture/chatgpt-browser-tool.md`.

Признаны актуальными без правок: Secret Store (#860, `docs/security/cloudru-secret-management.md`), RDC deployment (#861), Cloud.ru Container Apps (`scripts/cloudru_deploy.py`), CI fail-closed gate, контракт backup/restore (#780, #857).

## Текущий режим: инкрементальная поддержка

Каждые 6 часов проверяются PR, смерженные в master с прошлой проверки. Docs меняются только при реальном дрейфе.

Каждый проход отчитывается: проверенный диапазон master, проверенные смерженные PR, затронутые docs, что изменено, что оставлено как актуальное, какие открытые PR проигнорированы как не shipped.

## Изменения после снимка `d365c05` и их влияние на docs

| PR | docs в master | что ещё нужно |
|---|---|---|
| #920 ownership → `alice.memory` | `docs/memory/overview.md` обновлён в самом PR | ничего |
| #921 identity → file-native aggregate | `docs/memory/overview.md` обновлён в самом PR | строка 8 всё ещё пишет, что Wave 1 «переводит» `user_identity.py`, хотя #921 смержен; раздел «Статус» уже верный |
| #919, #923 Alice Dev | отдельной страницы нет | после cutover по #906 описать Alice Dev и legacy RDC в `docs/production-deployment.md` (см. [decisions.md](decisions.md)) |
| #918 | не требует docs | ничего |

## Watch list

После merge любого из этих пунктов нужна сверка docs:

| пункт | состояние на `d1c235b` |
|---|---|
| PR #865 chat/history roles | open, не смержен |
| PR #868 short-token через Secret Store | open, не смержен |
| #863 DeepSeek как провайдер | Issue open; до merge реализации описывать только как planned |
| #755 Secret Store boundary и миграция legacy credentials | Issue open |
| #776 Memory DB | Issue open; Wave 1 идёт: ownership (#920) и identity (#921) смержены, sessions/profiles и memory extraction впереди |
| #906 Alice Dev cutover и вывод legacy RDC | Issue open; деплой Alice Dev и MCP-смоук прошли |
| #913 шифрование backup | Issue open |

## Открытые docs-PR

| PR | что делает | основание |
|---|---|---|
| #871 | помечает SSH production deployment как deprecated | #869; перед merge сверить текст с пересмотром lifecycle (Alice Dev, legacy RDC) |
| #884 | `docs/integrations/cloudru-docs-mirror.md`: статус planned → not_planned | #428 закрыт как not planned, crawler-а в master нет |
| #885 | документирует workflow Work Map / trips.db | переоткрытый #470 |
| #886 | отделяет runtime agents от repository development roles | переоткрытый #569 |

Все четыре в draft и не смержены, поэтому их содержание пока не shipped.

## Известные расхождения docs

- `docs/production-deployment.md` описывает только SSH (#869, PR #871) и не знает про Alice Dev (#906).
- `docs/integrations/cloudru-docs-mirror.md` пишет «Статус: planned» (PR #884).
- Нет страницы Work Map (#470, PR #885) и навигации по role-based office (#569, PR #886).
- `docs/memory/overview.md:8` отстаёт от #921 (см. таблицу выше).
- В теле Issue #409 устарели описания #791 и #843.
