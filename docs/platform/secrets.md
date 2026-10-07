# Secrets Configuration

Status: **reference-only platform configuration**. Canonical Secret Store architecture and consumer migration are owned by #755.

Alice Platform configuration may name secrets, but it must never become a second credential database or imply that a secret value is already provisioned/resolvable merely because a logical reference exists in YAML.

## Current configuration contract

`config/alice/secrets.yaml` contains logical references only:

```yaml
oauth:
  client_secret: alice/prod/oauth-client-secret
  signing_key: alice/prod/oauth-signing-key

chrome:
  oauth_client_id: alice/prod/chrome-oauth-client-id
  oauth_client_secret: alice/prod/chrome-oauth-client-secret
  github_client_id: alice/prod/github-client-id
  github_client_secret: alice/prod/github-client-secret
  github_allowed_ids: alice/prod/github-allowed-ids
```

These strings are desired-state names. They are **not** secret values and, by themselves, are not proof that a corresponding remote secret/version exists.

## Canonical runtime boundary

The provider-neutral foundation lives in `secret_store/core.py`:

- `SecretRef` carries provider, secret ID, immutable version ID and purpose metadata;
- `SecretValue` is opaque on string/repr surfaces and cannot be serialized;
- `SecretResolver` defines the resolution boundary;
- `SecretResolutionError` exposes typed safe failure codes.

The already-shipped Cloud.ru implementation and pinned-version/rollback behavior are documented in [Cloud.ru Secret Management](../security/cloudru-secret-management.md). #755 owns generalization to all supported backends and migration of credential consumers.

## What current platform validation proves

`alice_platform/config.py` rejects plaintext-like secret material and enforces reference/lane safety rules. In particular:

- PEM-looking values are rejected;
- long base64-like values are rejected;
- values inside the secrets section must use the `alice/` reference namespace;
- the test lane may not reference `alice/prod/*`;
- identical tracked secret references may not be shared across lanes.

Validation does **not** prove:

- that Cloud.ru Secret Management contains the referenced secret;
- that a version is active;
- that runtime IAM can read it;
- that a particular application consumer has migrated to `SecretResolver`;
- that rotation, revocation or rollback has been exercised live.

For #755 acceptance, `SKIPPED` is not evidence. The target secret/reference/version path must explicitly PASS.

## Runtime plaintext rule

Resolved plaintext may exist only immediately around an authorized backend/tool operation. It must not enter prompts, ordinary tool results, durable state, logs or ExecutionTrace.

There is no approved plaintext fallback when canonical resolution fails.

## Bootstrap credentials

Credentials required to reach a remote secret manager are bootstrap material. They must be minimized and least-privilege and cannot themselves depend on the same remote secret lookup. The Cloud.ru-specific bootstrap/read boundary is described in the security document linked above.

## Rotation and migration

Rotation is explicit version switching, not an automatic quarterly job implemented by Alice Platform.

For each consumer, #755 requires:

1. create/import the secret in an approved backend;
2. persist only reference/version metadata;
3. switch the consumer to the canonical resolver;
4. verify success plus missing/revoked/unavailable failures;
5. stop new legacy value writes;
6. remove the legacy value path only after verified cutover.

Do not claim a consumer is migrated merely because its logical name appears in `config/alice/secrets.yaml`.

## Current limitations

The migration is incomplete. Current-master consumers still need inventory/cutover under #755, including supported provider credentials, GitHub OAuth secret paths, short-token authentication and other confirmed secret-bearing consumers.

Platform #783 may provision permissions/references, but it does not own a competing secret backend. Durable state #776 stores secret reference/version metadata only.

## Future work

- complete provider-neutral resolver adapters required by #755;
- migrate supported consumers one at a time;
- remove verified legacy durable secret-value writes;
- add provider-backed acceptance evidence for reference/version resolution;
- add rotation/revocation automation only after the explicit version-switch contract is proven.
