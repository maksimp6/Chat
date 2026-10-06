# Handoff: сверка документации и аудит закрытых Issues

Пакет для передачи контекста другому ассистенту (например, ChatGPT). Состояние на 2026-10-06, master `d365c05`.

Начинать чтение с этого файла. Он самодостаточен: остальные файлы дают детали и evidence.

## Что в пакете

| файл | о чём |
|---|---|
| [documentation-maintenance.md](documentation-maintenance.md) | Сверка docs с master: что сделано, правила, watch list, открытые docs-PR |
| [decisions.md](decisions.md) | Решения владельца: отказ от SSH-деплоя (#869), шифрование backup Memory DB (#913) |
| [closed-issues-audit/summary.md](closed-issues-audit/summary.md) | Итог аудита 268 закрытых Issues |
| [closed-issues-audit/batch-1.md](closed-issues-audit/batch-1.md) … [batch-7.md](closed-issues-audit/batch-7.md) | Построчные таблицы аудита с evidence |

## Коротко

1. **Сверка документации.** Полный аудит дрейфа docs закончен 2026-10-06, исправления смержены в PR #867 (Memory DB overview и OAuth `offline_access`). Дальше идёт только инкрементальная поддержка: каждые 6 часов проверяются новые merge в master.
2. **Аудит закрытых Issues.** Проверены все 268 CLOSED Issues. 6 переоткрыты (#538, #869, #545, #226, #470, #569), остаток scope ещё ряда Issues дописан в открытые канонические Issues.
3. **Решения владельца.** Production lifecycle только через `cloudru-rdc.yml`, SSH-путь устарел (#869). Backup Memory DB обязательно шифруется на стороне приложения до загрузки (#913).

## Правила работы (действуют и для следующего исполнителя)

- Shipped считается только то, что смержено в master. Открытые PR не описываются в docs как реализованное.
- Не делать PR ради косметики или только changelog.
- Не дублировать в docs машиночитаемые источники (например, `config/integrations/catalog.json`), а ссылаться на них.
- Пробелы реализации оформлять Issues, а не текстом в docs.
- Каждое изменение docs ссылается на merge PR, код/конфиг/тесты и затронутый doc.
- Только чистая ветка и PR, никогда не пушить в master, без auto-merge.

## Открытые хвосты на 2026-10-06

| что | состояние |
|---|---|
| PR #871 (SSH помечен deprecated) | draft, ждёт ревью владельца |
| PR #884 (зеркало Cloud.ru → not_planned) | draft |
| PR #885 (Work Map / trips.db, #470) | draft |
| PR #886 (runtime agents vs repository roles, #569) | draft |
| #913 шифрование backup | open; `agent_memory/backup.py` в master пока выгружает bundle без шифрования |
| #545, #226, #538 | open, нужна реализация, не docs |
