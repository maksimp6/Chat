# Provider API-key lifecycle and rotation

Alice Pro keeps one backend-owned runtime API key per provider. The credential
store is provider-aware, encrypted at rest, and never exposes plaintext keys
through metadata, traces, logs, or frontend responses.

## Providers

### Yandex Cloud

- Runtime authentication uses `Authorization: Api-Key <API_KEY>`.
- A replacement key is created through Yandex IAM.
- Alice Pro validates the replacement against the configured Yandex Responses endpoint.
- After validation, the replacement is promoted and the old provider key is revoked.
- The existing Yandex deployment lifecycle remains 12 hours with rotation in the final hour.

### Cloud.ru Foundation Models

- Runtime authentication uses `Authorization: Api-Key <API_KEY>` against
  `https://foundation-models.api.cloud.ru/v1`.
- The Foundation Models key is created for a service account with the
  Foundation Models service selected.
- Cloud.ru supports key lifetimes from one day to one year. Alice Pro defaults
  to a one-day lifetime via `CLOUDRU_KEY_TTL_DAYS=1`, so the key is rotated daily.
- Cloud.ru documents API-key reissue: the Key Secret and expiry change while
  the key ID remains unchanged.
- Alice Pro therefore uses in-place reissue for Cloud.ru. The new secret is
  validated against Foundation Models before the local credential is promoted.
  The same Cloud.ru provider resource is not revoked after reissue.
- Automated Cloud.ru rotation additionally requires a provider key ID and
  server-side IAM management credentials (`CLOUDRU_IAM_KEY_ID` /
  `CLOUDRU_IAM_KEY_SECRET`). Without them, the UI reports rotation as
  unsupported rather than claiming that rotation works.

## Credential configuration UI

The current modal in `static/provider_credentials.js` contains:

- **Yandex Cloud API key**
- **Yandex Cloud Project ID** (required together with the Yandex key)
- **Cloud.ru API key**

It sends `PUT /api/provider-credentials`. The backend validates supplied keys
with the provider before saving them. This manual path stores a 12-hour credential
window and no provider resource ID; saving a key does not establish managed
rotation support.

A separate `POST /api/provider-credentials/cloudru/bootstrap` route accepts IAM
management credentials and an existing service-account UUID as form data. It
creates and validates a Foundation Models key, then stores the management and
runtime credentials. Its lifetime follows `CLOUDRU_KEY_TTL_DAYS` (default: one
day). The manual API-key modal does not invoke this bootstrap route.

The service account must already exist and have an appropriate project role.
The bootstrap route does not create a service account. A separate
`POST /api/provider-credentials/cloudru/service-accounts` route lists accounts.

## Security

Plaintext API keys must never appear in:

- frontend JSON responses;
- Execution Trace;
- normal application logs;
- exception messages;
- GitHub issues/PRs;
- persistent metadata.

Persistent records store encrypted secrets plus non-secret provider IDs and
SHA-256 fingerprints.

## Rotation worker

Yandex:

```bash
python -m scripts.rotate_provider_key
```

Cloud.ru:

```bash
python -m scripts.rotate_cloudru_provider_key
```

Run these commands from the repository root with the application's dependencies
and environment configured. Schedule the appropriate worker at least hourly;
starting Flask does not schedule it. The Cloud.ru worker uses a one-day
key lifetime and rotates during the final hour of that daily lifetime. Each
worker validates the new/reissued secret before promoting it in the database.

## Runtime credential source

`provider_credentials.resolve_client_credential` reads the active credential
exclusively from the database. The Yandex request path additionally requires
`ALICE_PROVIDER_CREDENTIAL_KEY` and a non-empty Project ID in that record.

Environment-only `YANDEX_API_KEY`, `YC_API_KEY`, `YANDEX_PROJECT_ID` or
`CLOUDRU_API_KEY` values are not a fallback for this resolver. Their presence
in an environment template does not bootstrap the current web runtime.
Configure the credential through the UI/API before sending a model request.

Set a persistent `ALICE_PROVIDER_CREDENTIAL_KEY` before saving credentials and
retain it with deployment configuration. The provider crypto helper derives
encryption material from this value; it does not generate a deployment key.
If the same value is used by Key Manager, it must also satisfy that component's
Fernet-key format.

## Administration endpoint security

The provider-credential API is administrative. Set `ALICE_PROVIDER_CREDENTIALS_TOKEN` for explicit Bearer-token authorization. When Alice Pro short-token authentication is enabled, the existing authenticated short-token session is reused. Remote requests are rejected when neither mechanism is configured.
