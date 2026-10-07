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


- Runtime authentication uses `Authorization: Api-Key <API_KEY>` against
- The Foundation Models key is created for a service account with the
  Foundation Models service selected.
  the key ID remains unchanged.
  validated against Foundation Models before the local credential is promoted.
  management credentials stored in the database by the bootstrap route below.
  in the environment alone does not enable it.
  IAM client, so the UI currently shows `rotation.supported: false` even when
  bootstrap credentials are stored and the worker can rotate the key.

## Credential configuration UI

The current modal in `static/provider_credentials.js` contains:

- **Yandex Cloud API key**
- **Yandex Cloud Project ID** (required together with the Yandex key)

It sends `PUT /api/provider-credentials`. The backend validates supplied keys
with the provider before saving them. This manual path stores a 12-hour credential
window and no provider resource ID; saving a key does not establish managed
rotation support.

management credentials and an existing service-account UUID as form data. It
creates and validates a Foundation Models key, then stores the management and
day). The manual API-key modal does not invoke this bootstrap route.

The service account must already exist and have an appropriate project role.
The bootstrap route does not create a service account. A separate

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


```bash
```

Run these commands from the repository root with the application's dependencies
and environment configured. Schedule the appropriate worker at least hourly;
key lifetime and rotates during the final hour of that daily lifetime. Each
worker validates the new/reissued secret before promoting it in the database.

## Runtime credential source

`provider_credentials.resolve_client_credential` reads the active credential
exclusively from the database. The Yandex request path additionally requires
`ALICE_PROVIDER_CREDENTIAL_KEY` and a non-empty Project ID in that record.

Environment-only `YANDEX_API_KEY`, `YC_API_KEY`, `YANDEX_PROJECT_ID` or
in an environment template does not bootstrap the current web runtime.
Configure the credential through the UI/API before sending a model request.

Set a persistent `ALICE_PROVIDER_CREDENTIAL_KEY` before saving credentials and
retain it with deployment configuration. The provider crypto helper derives
encryption material from this value; it does not generate a deployment key.
If the same value is used by Key Manager, it must also satisfy that component's
Fernet-key format.

## Administration endpoint security

The provider-credential API is administrative. Set `ALICE_PROVIDER_CREDENTIALS_TOKEN` for explicit Bearer-token authorization. When Alice Pro short-token authentication is enabled, the existing authenticated short-token session is reused. Remote requests are rejected when neither mechanism is configured.
