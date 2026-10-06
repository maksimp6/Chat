# Сверка документации с master

Состояние на 2026-10-06, master `d365c05`.

## Базовый аудит (завершён)

Аудит дрейфа документации прошёл по master `bd120d4`…`943bf8e` и охватил Memory DB и backup/restore, Secret Store, Chrome/RDC deployment, OAuth, инфраструктуру Cloud.ru и CI.

Найдены и исправлены в PR #867 (смержен):

1. `docs/memory/overview.md` утверждал, что память хранится in-memory. На деле хранение SQL-backed (`memory_manager.py`, `memory_extractor.py`). Документ исправлен, упомянута реализация FileMemoryDB (#856, #857) и план миграции #776 (Wave 1).
2. `deploy/chrome-worker/oauth.mjs` реализует scope `offline_access` (refresh token 30 дней, access token 15 минут, вход через GitHub, регистрация клиентов с вытеснением через 7 дней), а документации не было. Добавлен раздел OAuth в `docs/architecture/chatgpt-browser-tool.md`.

Признаны актуальными без правок: Secret Store (#860, `docs/security/cloudru-secret-management.md`), RDC deployment (#861), Cloud.ru Container Apps (`scripts/cloudru_deploy.py`), CI fail-closed gate, контракт backup/restore (#780, #857).

## Текущий режим: инкрементальная поддержка

Каждые 6 часов проверяются PR, смерженные в master с прошлой проверки. Docs меняются только при реальном дрейфе.

Каждый проход отчитывается: проверенный диапазон master, проверенные смерженные PR, затронутые docs, что изменено, что оставлено как актуальное, какие открытые PR проигнорированы как не shipped.

## Watch list

После merge любого из этих пунктов нужна сверка docs:

| пункт | состояние на 2026-10-06 |
|---|---|
| PR #865 chat/history roles | open, не смержен |
| PR #868 short-token через Secret Store | open, не смержен |
| #863 DeepSeek как провайдер | Issue open; до merge реализации описывать только как planned |
| #755 Secret Store boundary и миграция legacy credentials | Issue open |
| #776 Memory DB (durable file-native, Wave 1) | Issue open; после внедрения FileMemoryDB обновить `docs/memory/overview.md` |
| #913 шифрование backup | Issue open |
| Production lifecycle RDC/browser | см. [decisions.md](decisions.md), #869 |

## Открытые docs-PR

| PR | что делает | основание |
|---|---|---|
| #871 | помечает SSH production deployment как deprecated | решение по #869 |
| #884 | `docs/integrations/cloudru-docs-mirror.md`: статус planned → not_planned | #428 закрыт как not planned, crawler-а в master нет |
| #885 | документирует workflow Work Map / trips.db | переоткрытый #470 |
| #886 | отделяет runtime agents от repository development roles | переоткрытый #569 |

Все четыре в draft и не смержены, поэтому их содержание пока не shipped.

## Известные расхождения docs (из аудита Issues)

- `docs/production-deployment.md` описывает только SSH (#869, PR #871).
- `docs/integrations/cloudru-docs-mirror.md` пишет «Статус: planned» (PR #884).
- Нет страницы Work Map (#470, PR #885) и навигации по role-based office (#569, PR #886).
- В теле Issue #409 устарели описания #791 и #843.
