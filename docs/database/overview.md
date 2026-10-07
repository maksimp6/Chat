# База данных и модели

Alice Pro находится в переходе от legacy SQL persistence к canonical file-native Memory DB (#776). Поэтому термин «база данных» сейчас означает несколько разных границ, и их нельзя смешивать.

## Текущее состояние

- SQLite/PostgreSQL остаются legacy/application storage для ещё не мигрированных потребителей.
- FileMemoryDB (`agent_memory/file_memory_db.py`) — durable file-native engine с recovery/compaction и verified backup/restore foundation.
- Conversation ownership и user identity/GitHub mapping уже имеют file-native authoritative stores после bounded migration.
- Memory extraction (`memory_manager.py`, `memory_extractor.py`) всё ещё использует SQL.
- Наличие FileMemoryDB не означает, что SQL уже полностью удалён из runtime.

## Граница миграции

Каждый consumer переводится отдельно:

1. определить typed storage boundary;
2. импортировать legacy state ограниченным migration path;
3. проверить equivalence/integrity;
4. атомарно опубликовать новый authoritative state;
5. после migration marker не возвращаться к legacy reads;
6. удалить legacy write/value path только после доказанного cutover.

Canonical contract и оставшиеся consumers принадлежат #776.

## Что нельзя утверждать

До завершения #776 документация не должна утверждать ни «всё хранится в SQLite/PostgreSQL», ни «SQL полностью удалён». Оба утверждения неверны для текущего master.

Подробный статус памяти: [Memory overview](../memory/overview.md).
