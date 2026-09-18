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
