# Работа с памятью в Alice Pro

## Текущее состояние (master 2026-10-06)

Система памяти находится в переходе к дurable file-native Storage Engine. На данный момент:

- **Извлечение памяти** использует SQL-backed модель: `memory_manager.py` и `memory_extractor.py` создают таблицу `global_memory` для хранения фактов, извлечённых из диалогов
- **Durable Storage Engine** (FileMemoryDB) реализован в `agent_memory/file_memory_db.py` (PR #856) с полноценной backup/restore (PR #857); первый Wave 1 consumer — `conversation_ownership.py` — переносится на file-native ownership с verified one-time import из legacy SQL
- **Целевая архитектура** описана в [issue #776](https://github.com/maksimp6/Chat/issues/776): одна дurable file-native Memory DB с crash recovery, deterministic tests и миграцией от SQL

## Компоненты

### Текущие (SQL-backed)

- `memory_manager.py` — управление конфигурацией и доступом к памяти через SQLite/PostgreSQL
- `memory_extractor.py` — извлечение фактов из диалогов, сохранение в SQL таблицу `global_memory`
- `db.py` — связь с `ALICE_DATABASE_URL`, инициализация схемы

### File-native foundation и Wave 1

- `agent_memory/file_memory_db.py` — append-only JSONL файл с fsync-гарантиями, recovery и compaction
- `agent_memory/backup.py` — verified backup/restore с manifest и SHA256 checksum
- `agent_memory/conversation_ownership_store.py` — durable typed ownership repository
- `agent_memory/conversation_ownership_migration.py` — bounded legacy SQL import; после migration marker runtime ownership SQL больше не читает
- `tests/test_file_memory_db.py`, `tests/test_memory_backup.py`, `tests/test_conversation_ownership_memory.py` — доказательства durability, recovery и первого consumer cutover

## Миграция (issue #776)

Wave 1 интегрирует FileMemoryDB в `memory_manager` и заменит SQL хранилище дurable engine для:
- пользовательской идентичности (`user_identity.py`)
- владения разговорами (`conversation_ownership.py`)
- сессий и профилей (`session_manager.py`)
- истории сообщений и памяти (`memory_manager.py`, `memory_extractor.py`)

Миграция требует:
1. Доказательства crash recovery и integrity на новом engine
2. Shadow verification пока старый источник (SQL) остаётся authoritative
3. Atomic cutover только после verified equivalence
4. Explicit rollback capability

**Статус**: durable engine и backup/restore готовы; Wave 1 начат с conversation ownership. User identity, sessions и memory extraction всё ещё требуют миграции.

## Сценарии использования (текущие)

1. **Добавление данных**: backend вызывает `memory_extractor.extract_and_save_facts()` для сохранения в SQL `global_memory`
2. **Извлечение памяти**: `memory_manager.get_controlled_memory_summary()` читает из SQL с учётом конфигурации категорий
3. **Очистка**: SQL запросы удаляют устаревшие факты по `updated_at`

## Интеграция с другими компонентами

- **Агенты**: используют извлечённые факты как часть контекста (через `memory_manager`)
- **Бэкенд**: сохраняет факты из диалогов в памяти для последующих запросов
- **API**: предоставляет методы для читать/писать конфигурацию памяти (`load_memory_config`, `save_memory_config`)

Для детального изучения миграционной стратегии см. [memory-db-v1-contract.md](memory-db-v1-contract.md) и [issue #776](https://github.com/maksimp6/Chat/issues/776).