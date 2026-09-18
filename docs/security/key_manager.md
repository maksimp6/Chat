# Менеджер криптографии и ключей

`key_manager.py` — единая граница управления секретами Alice Pro. Он хранит секреты в зашифрованном виде, а приложения работают с непрозрачным `key_ref`.

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
