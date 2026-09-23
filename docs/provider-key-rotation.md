# Provider API-key lifecycle and rotation

Alice Pro keeps one backend-owned runtime API key per provider. The credential
store is provider-aware, encrypted at rest, and never exposes plaintext keys
through metadata, traces, logs, or frontend responses.

## Providers

### Yandex Cloud

- Runtime authentication uses \`Authorization: Api-Key <API_KEY>\`.\n- Managed keys must include the \`yc.ai.foundationModels.execute\` scope for the AI Studio runtime path.
- A replacement key is created through Yandex IAM.
- Alice Pro validates the replacement against the Yandex AI Studio `/models` catalog endpoint before promotion, so a key without AI Studio access is rejected without invoking a generation model.
- After validation, the replacement is promoted and the old provider key is revoked.
- The existing Yandex deployment lifecycle remains 12 hours with rotation in the final hour.

### Cloud.ru Foundation Models

- Runtime authentication uses \`Authorization: Api-Key <API_KEY>\` against
  \`https://foundation-models.api.cloud.ru/v1\`.
- The Foundation Models key is created for a service account with the
  Foundation Models service selected.
- Cloud.ru supports key lifetimes from one day to one year. Alice Pro defaults
  to a one-day lifetime via \`CLOUDRU_KEY_TTL_DAYS=1\`, so the key is rotated daily.
- Cloud.ru documents API-key reissue: the Key Secret and expiry change while
  the key ID remains unchanged.
- Alice Pro therefore uses in-place reissue for Cloud.ru. The new secret is
  validated against Foundation Models before the local credential is promoted.
  The same Cloud.ru provider resource is not revoked after reissue.
- Automated Cloud.ru rotation additionally requires a provider key ID and
  server-side IAM management credentials (\`CLOUDRU_IAM_KEY_ID\` /
  \`CLOUDRU_IAM_KEY_SECRET\`). Without them, the UI reports rotation as
  unsupported rather than claiming that rotation works.

## Credential configuration UI

The provider credentials modal contains:

- **Yandex Cloud API key**
- **Cloud.ru IAM Key ID**
- **Cloud.ru IAM Key Secret**
- **Cloud.ru Service account ID**

The Cloud.ru runtime API key is not entered manually. Alice Pro creates it for the
existing service account, performs a real Foundation Models health check, and
stores the IAM management credentials and runtime credential encrypted on the
server.

A service account must already exist and have an appropriate project role before
the runtime key is created. Alice Pro does not enumerate or silently create
service accounts during provider bootstrap.

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

\`\`\`bash
python3 scripts/rotate_provider_key.py
\`\`\`

Cloud.ru:

\`\`\`bash
python3 scripts/rotate_cloudru_provider_key.py
\`\`\`

Run the appropriate worker at least hourly. The Cloud.ru worker uses a one-day
key lifetime and rotates during the final hour of that daily lifetime. Each
worker validates the new/reissued secret before promoting it in the database.

## Environment precedence

Environment variables provide bootstrap values when the provider has no active
database credential. Once an encrypted active credential exists, request-time
resolution prefers the database record.

Use provider-specific names:

- \`YANDEX_API_KEY\` (legacy \`YC_API_KEY\` remains supported)
- \`CLOUDRU_API_KEY\`

Do not introduce a generic \`API_KEY\` for both providers.

## Administration endpoint security

The provider-credential API is administrative. Set `ALICE_PROVIDER_CREDENTIALS_TOKEN` for explicit Bearer-token authorization. When Alice Pro short-token authentication is enabled, the existing authenticated short-token session is reused. Remote requests are rejected when neither mechanism is configured.
