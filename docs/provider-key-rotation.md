# Global Yandex API-key rotation

Alice Pro uses one backend-owned Yandex provider key for the deployment. Keys
are not issued per user.

## Lifecycle

- A newly issued key is valid for **12 hours** (`KEY_TTL`).
- Rotation becomes due during the final hour (`ROTATE_BEFORE`).
- The replacement is inserted and promoted inside the caller's database
  transaction; the partial unique index permits only one `active` key.
- The previous provider key must be revoked through the injected
  `YandexKeyProvider` adapter after promotion.
- Plaintext keys exist only during the provider/encryption boundary and must
  not be logged, included in traces, or returned from HTTP endpoints.

## Deployment integration

`provider_key_rotation.rotate_active_key` requires:

1. A database adapter and an explicit transaction.
2. A deployment-specific `YandexKeyProvider` implementation backed by the
   deployment's IAM credentials.
3. An encryption implementation supplied through `encrypt`.
4. A scheduler or worker that invokes rotation before expiry and alerts when
   rotation fails.

The legacy environment `API_KEY` remains a bootstrap fallback when no
credential store is supplied. Production deployments should use the global
credential table and an encrypted secret store.

## Trace attribution

Каждая операция Responses API получает идентификатор реально использованного
глобального ключа. В trace сохраняются Yandex API-key resource ID, fingerprint
для bootstrap-ключа, время выпуска/истечения и project ID. Секрет ключа и
IAM-токен не сохраняются.

Идентификатор пишется в `provider_key`, историю `provider_keys`, а также в
каждый элемент `api_requests[]` и `responses[]`. Поэтому trace сохраняет
аудит даже при смене ключа между запросами одного долгого invocation.

## Worker

Запускайте `python3 scripts/rotate_provider_key.py` через cron/systemd
каждый час. Worker ротирует ключ только в последнем часу его 12-часового
срока. Yandex Cloud IAM API создаёт ключ через `POST /iam/v1/apiKeys` и
удаляет старый через `DELETE /iam/v1/apiKeys/{apiKeyId}`.
