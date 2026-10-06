# Handoff: документация, аудит Issues и актуальное состояние платформы

Пакет для передачи контекста другому исполнителю. Актуальный верхний снимок: 2026-10-06, master `d1c235b`.

Начинать с этого файла. Исторические файлы `closed-issues-audit/batch-*.md` намеренно сохраняют SHA и состояние на момент своего аудита; их не следует переписывать под текущий master.

## Что в пакете

| файл | о чём |
|---|---|
| [documentation-maintenance.md](documentation-maintenance.md) | Сверка docs с master, режим поддержки, watch list и открытые docs-PR |
| [decisions.md](decisions.md) | Действующие архитектурные решения и уточнения после исходного snapshot |
| [closed-issues-audit/summary.md](closed-issues-audit/summary.md) | Итог аудита 268 закрытых Issues |
| [closed-issues-audit/batch-1.md](closed-issues-audit/batch-1.md) … [batch-7.md](closed-issues-audit/batch-7.md) | Исторические evidence-таблицы аудита |

## Текущее состояние

1. **Memory DB.** Durable FileMemoryDB и verified backup/restore смержены через #856/#857. Wave 1 уже начат: #920 перенёс conversation ownership в `alice.memory`, #921 перенёс user identity + GitHub mapping в один atomic file-native aggregate. Runtime identity после verified import больше не читает SQL. Следующий Wave 1 slice — sessions/profile clones.
2. **Alice Dev.** Lightweight coding/MCP worker отделён от legacy RDC. После #923 live deploy на Cloud.ru успешно завершился в run `37490250904`: deploy candidate, public health check и MCP end-to-end smoke прошли. Worker использует 0.2 vCPU / 512 MiB, min=0/max=1; Chromium в образ не входит.
3. **Legacy RDC.** Старый Remote Desktop Commander пока остаётся рабочим fallback и не выключается до отдельного cutover. Термин RDC не использовать для Alice Dev.
4. **Аудит закрытых Issues.** Проверены 268 CLOSED Issues; результаты партий остаются историческим evidence и не означают, что их SHA равен текущему master.
5. **Backup encryption.** #913 остаётся обязательным: backup должен шифроваться приложением до загрузки; provider-side encryption — только второй слой.

## Изменения после исходного snapshot `d365c05`

- #920: conversation ownership → FileMemoryDB.
- #921: user identity + GitHub accounts → atomic file-native aggregate; общий process-local writer для `alice.memory`.
- #923: deploy-only recovery Cloud.ru Container Apps после HTTP 499; повторный live deploy Alice Dev полностью зелёный.
- Alice Dev теперь подтверждён не только unit tests: public MCP smoke реально прошёл против Cloud.ru.

## Правила работы

- Shipped считается только то, что смержено в master и, для инфраструктуры, прошло требуемую live-проверку.
- Не описывать открытые PR как реализованное состояние.
- Не переписывать исторический evidence под новый master; сверху добавлять актуальный snapshot.
- Не делать PR ради косметики или changelog.
- Машиночитаемые факты должны жить в config/code, docs объясняют контракт и evidence.
- Пробелы реализации оформлять Issues.
- Только clean branch → PR → exact-head CI; без прямого push в protected master.

## Открытые хвосты

| что | состояние |
|---|---|
| #776 Memory DB | open; ownership и identity уже file-native, sessions/profile/memory consumers ещё мигрируют |
| #913 encryption at rest | open; plaintext backup bundle нельзя считать финальным production contract |
| Alice Dev cutover | live deploy + MCP smoke зелёные; legacy RDC пока не выключен |
| OAuth для MCP | временный short-token ещё используется; конечная цель — убрать ручное копирование через OAuth |
| PR #871, #884, #885, #886 | исторически были draft в исходном snapshot; перед использованием проверить текущее состояние в GitHub |
