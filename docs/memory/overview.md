# Память Logos

## Целевая архитектура PR #1191 (черновик)

Хранение данных постепенно переводится на **единственный движок**
`kristall.store.Kristall`. Это синхронная file-native база с журналом,
проверяемыми коммитами, транзакциями, эксклюзивным writer-lock и штатными
`backup()/restore()`. Она не использует SQLite/PostgreSQL и не нуждается в
адаптерах, SQL-миграциях или совместимости со старым API.

Общий владелец экземпляров `Kristall` находится в
`agent_memory/client.py`: `get_memory_client(path)` и
`close_memory_clients(path)`. Не создавайте параллельный реестр владельцев.

### Что уже переведено в ветке PR #1191

- `agent_memory/repository.py` сохраняет агентские записи отдельными ключами
  `agent_memory:<memory_id>`.
- `conversation_ownership.py` и `agent_memory/conversation_ownership_store.py`
  фиксируют владельцев через атомарные операции на ключах `conversation_owner:`.
- `identity/user_identity.py` сохраняет пользователей, токены и GitHub account mapping
  в Kristall без чтения SQL.
- `session_manager.py` и `invocation/manager.py` используют ключи
  `runtime_session:` и `runtime_invocation:` для жизненного цикла,
  трассировок и восстановления после повторного запуска.
- `memory_extractor.py` записывает извлечённые факты под ключами
  `global_fact:`; `memory_manager.py` хранит настройки и предоставляет
  операции чтения/очистки без SQL.
- `/api/memory/manage` читает факты напрямую из Kristall.

Удалены альтернативные `FileMemoryDB`, его backup-код, SQL-миграции и
PostgreSQL deployment assets. Для резервного копирования используется
непосредственно `Kristall.backup()` и `restore()`.

### Что НЕ готово

Этот PR не готов к merge, пока `db.py`, `db_backend.py` и другие
SQL-потребители не будут удалены или полностью переведены на Kristall.
Нельзя считать успешный smoke доказательством полного E2E и отсутствия SQL.
Контракт `tests/test_sql_free_contract.py` должен оставаться красным,
пока реальные SQL-импорты существуют. Не ослаблять контракт ради зелёного CI.

Тестирование и выпуск: `AGENTS.md`, exact-head GitHub Actions,
`tests/test_sql_free_contract.py`, `tests/test_memory_backup.py` и
функциональные тесты Kristall. PR #1191 остаётся draft; без согласования
не трогать `master`, merge и реальные данные пользователей.
