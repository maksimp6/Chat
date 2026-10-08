# Менеджер криптографии и ключей

`key_manager.py` — shipped legacy/local encryption boundary Alice Pro. Он хранит секреты в зашифрованном виде и выдаёт `key_ref`, но **не является единственной или канонической Secret Store архитектурой**. Provider-neutral target contract находится в `secret_store/core.py` (`SecretRef`, `SecretValue`, `SecretResolver`) и мигрируется consumer-by-consumer в #755.

## Хранилище

Секрет шифруется Fernet до записи в SQLite. Ключ шифрования задаётся через `ALICE_KEY_MANAGER_KEY`; для совместимости с существующей конфигурацией также принимается `ALICE_PROVIDER_CREDENTIAL_KEY`.

Команда генерации ключа:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

## API

- `store_secret(...)` создаёт активный секрет и возвращает только метаданные.
- `read_secret(key_ref)` выдаёт plaintext только явному вызывающему коду.
- `list_keys(...)` и `get_metadata(...)` никогда не возвращают секрет.
- `rotate_key(...)` создаёт новый `key_ref` и отзывает старый.
- `revoke_key(...)` немедленно запрещает чтение ключа.

Метаданные содержат fingerprint SHA-256, но не само значение секрета.

## Политика

Ключи разделяются по provider, purpose, environment и owner. Секретные значения нельзя записывать в ExecutionTrace, обычные логи, ответы API или frontend storage.

Release signing keystore, Cloud.ru API keys, OAuth credentials и другие секреты должны использовать этот слой или внешний secret manager. Сам encryption key менеджера также является секретом и должен храниться только вне репозитория.

Для секретов, которыми управляет Cloud.ru Secret Management (внешнее хранилище с неизменяемыми версиями), см. [docs/security/cloudru-secret-management.md](cloudru-secret-management.md) — там описана отдельная граница: локально хранится только ссылка на закреплённую версию, а не сам секрет.

## Current coexistence

На current master одновременно существуют несколько secret-bearing механизмов: legacy `key_manager.py`, encrypted SQL `provider_credentials.py`, прямые environment consumers и canonical `secret_store/` resolver foundation. Это migration state, а не четыре равноправных целевых архитектуры.

Новый consumer должен использовать canonical Secret Store boundary. Legacy слой сохраняется только пока конкретный runtime consumer не прошёл verified cutover; после этого его старый durable plaintext/encrypted-value path удаляется отдельно.
