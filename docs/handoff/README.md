# Handoff: сверка документации и аудит закрытых Issues

Пакет для передачи контекста другому ассистенту (например, ChatGPT). Верхний слой обновлён 2026-10-06 на master `d1c235b` (после успешного деплоя Alice Dev, run 37490250904). Исходный снимок пакета был на master `d365c05`.

Начинать чтение с этого файла. Он самодостаточен: остальные файлы дают детали и evidence.

## Что в пакете

| файл | о чём |
|---|---|
| [documentation-maintenance.md](documentation-maintenance.md) | Сверка docs с master: что сделано, правила, watch list, открытые docs-PR |
| [decisions.md](decisions.md) | Решения владельца: SSH-деплой выведен (#869), lifecycle Alice Dev и legacy RDC (#906), шифрование backup Memory DB (#913) |
| [closed-issues-audit/summary.md](closed-issues-audit/summary.md) | Итог аудита 268 закрытых Issues |
| [closed-issues-audit/batch-1.md](closed-issues-audit/batch-1.md) … [batch-7.md](closed-issues-audit/batch-7.md) | Построчные таблицы аудита с evidence |

Аудит закрытых Issues выполнен на master `dfda42c` и оставлен как есть. Это историческое evidence того, что проверялось на том SHA, а не описание текущего master.

## Коротко

1. **Сверка документации.** Полный аудит дрейфа docs закончен 2026-10-06, исправления смержены в PR #867. Дальше идёт инкрементальная поддержка: каждые 6 часов проверяются новые merge в master.
2. **Аудит закрытых Issues.** Проверены все 268 CLOSED Issues. 6 переоткрыты (#538, #869, #545, #226, #470, #569), остаток scope ещё ряда Issues дописан в открытые канонические Issues.
3. **Решения владельца.** SSH-деплой выведен (#869). Лёгкий MCP-воркер теперь называется Alice Dev и задеплоен рядом с legacy RDC, которое выводится по плану #906. Backup Memory DB обязательно шифруется на стороне приложения до загрузки (#913).

## Изменения после снимка `d365c05`

Смержено в master `d365c05`…`d1c235b`:

| PR | что изменилось |
|---|---|
| #918 | скрипт деплоя MCP-кандидата запускается напрямую |
| #919 | лёгкий RDC-воркер переименован в Alice Dev (`alice-dev-*`), ресурсы 0.2 vCPU / 512 MiB, min=0, max=1; legacy `rdc-*` и browser/desktop lifecycle не тронуты |
| #920 | Memory DB Wave 1 (#776): владение разговорами перенесено в `alice.memory` (FileMemoryDB) с проверяемым одноразовым импортом из SQL; после migration marker SQL не читается |
| #921 | Memory DB Wave 1 (#776): user identity и GitHub mapping перенесены в один атомарный file-native aggregate; `user_identity.py` больше не использует runtime SQL; импорт из SQLite и PostgreSQL |
| #923 | деплой Alice Dev переживает HTTP 499 от Cloud.ru Container Apps: повтор только для 499, затем строгая инвентаризация; create только при доказанном отсутствии |

После #923 workflow «Deploy Alice Dev worker» на `d1c235b` прошёл (run 37490250904): кандидат задеплоен, публичная MCP-проверка end-to-end зелёная. Переключение на Alice Dev и остановка legacy RDC по #906 ещё не выполнены.

Memory DB Wave 1 реально начат: ownership и identity уже file-native; sessions/profiles и memory extraction пока на SQL (`docs/memory/overview.md`).

## Правила работы (действуют и для следующего исполнителя)

- Shipped считается только то, что смержено в master. Открытые PR не описываются в docs как реализованное.
- Не делать PR ради косметики или только changelog.
- Не дублировать в docs машиночитаемые источники (например, `config/integrations/catalog.json`), а ссылаться на них.
- Пробелы реализации оформлять Issues, а не текстом в docs.
- Каждое изменение docs ссылается на merge PR, код/конфиг/тесты и затронутый doc.
- Только чистая ветка и PR, никогда не пушить в master, без auto-merge.

## Открытые хвосты на `d1c235b`

Состояния перепроверены через GitHub API 2026-10-06 после деплоя.

| что | состояние |
|---|---|
| PR #871 (SSH помечен deprecated) | draft; формулировки нужно сверить с пересмотром lifecycle (см. decisions.md) |
| PR #884 (зеркало Cloud.ru → not_planned) | draft |
| PR #885 (Work Map / trips.db, #470) | draft |
| PR #886 (runtime agents vs repository roles, #569) | draft |
| PR #865, PR #868 | open, не смержены |
| #906 переход на Alice Dev | open: деплой и MCP-смоук прошли, cutover и вывод legacy RDC впереди |
| #776 Memory DB | open, Wave 1 идёт |
| #913 шифрование backup | open; `agent_memory/backup.py` пока выгружает bundle без шифрования |
| #545, #226, #538 | open, нужна реализация, не docs |
