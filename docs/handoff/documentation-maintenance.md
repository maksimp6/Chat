# Сверка документации с master

Актуальный верхний снимок: 2026-10-06, master `d1c235b`.

## Базовый аудит

Исходный аудит дрейфа документации охватил Memory DB и backup/restore, Secret Store, Chrome/RDC deployment, OAuth, Cloud.ru и CI. PR #867 исправил найденные на тот момент расхождения.

Исторический snapshot был сделан на `d365c05`. Он больше не является текущим состоянием master.

## Существенные изменения после исходного snapshot

### Memory DB

- #920: conversation ownership перенесён в FileMemoryDB с verified one-time import из legacy SQL.
- #921: user identity + GitHub mapping перенесены в один atomic file-native aggregate.
- Runtime identity после migration marker SQL больше не читает.
- Общий runtime store обеспечивает один process-local writer/barrier и monotonic sequence на `alice.memory`.
- #776 остаётся open: sessions/profile clones и последующие memory consumers ещё требуют миграции.

Поэтому старое утверждение «FileMemoryDB реализован, но не интегрирован в приложение» больше неверно.

### Alice Dev / RDC

- Lightweight worker теперь **Alice Dev**, а не RDC.
- #923 исправил Cloud.ru HTTP 499 lookup при deploy.
- Live run `37490250904` на `d1c235b` полностью успешен: candidate deploy, public health, MCP end-to-end smoke.
- Legacy RDC остаётся fallback до отдельного cutover.
- Документация не должна смешивать Alice Dev с Remote Desktop Commander.

### Авторизация

- Alice Dev пока использует существующий short-token bootstrap.
- Это временное состояние; целевой UX — OAuth без ручного копирования token.
- Не документировать новые per-service manual tokens как желаемую архитектуру.

## Текущий режим: инкрементальная поддержка

После merge, который меняет runtime/infra contract, проверять:

1. изменился ли канонический config/code contract;
2. изменился ли shipped/live state;
3. устарели ли README/architecture/operations docs;
4. требуется ли обновление handoff;
5. не описывает ли открытый PR ещё не shipped состояние как факт.

Для инфраструктуры merge сам по себе недостаточен, если контракт требует live acceptance.

## Watch list

| пункт | состояние на master `d1c235b` |
|---|---|
| #776 Memory DB | Wave 1 частично shipped (#920/#921); продолжить sessions/profile/memory consumers |
| #913 backup encryption | open |
| Alice Dev | live deploy + MCP smoke зелёные; legacy RDC cutover ещё не выполнен |
| MCP OAuth | целевое состояние; short token пока временный bootstrap |
| Secret Store / credentials | следить, чтобы не возвращались новые обязательные ручные service tokens |

## Исторические docs-PR

Исходный snapshot перечислял #871, #884, #885 и #886 как draft. Перед любым решением по ним состояние нужно перечитать из GitHub; handoff не должен замораживать их статус навечно.

## Исторический аудит Issues

Файлы `closed-issues-audit/batch-1.md` … `batch-7.md` сохраняют master SHA, на котором проводилась конкретная проверка. Это evidence, а не обещание актуальности каждой строки на текущем master. Новые изменения фиксируются в верхнем handoff или новым audit delta, а не переписыванием старых партий.
