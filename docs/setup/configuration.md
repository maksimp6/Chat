# Configuration reference

Alice Pro has several configuration layers. Do not treat them as one flat set of
environment variables.

## Local process configuration

`.env.example` is the repository template for process environment variables.
Copy it to a private `.env` only for local development and remove unused
placeholder integrations. `config.py` loads the repository-root `.env`.

The template is the authoritative inventory for ordinary environment-variable
names; this page explains their ownership instead of duplicating every value.

### Application/runtime

Common process settings include:

- `HOST` / `PORT` — Flask bind settings used by the application entrypoint;
- `ALICE_DATABASE_URL` — selects legacy/shared PostgreSQL consumers when set;
- `ALICE_PROVIDER_CREDENTIAL_KEY` — compatibility encryption key for the
  current provider-credential store;
- quota/admin/auth variables under the `ALICE_*` namespace;
- provider endpoints and non-secret provider identifiers.

A variable appearing in `.env.example` does not mean every runtime consumer
uses it. For example, the web chat resolves its Yandex credential through the
provider credential store, while some legacy consumers still read provider
credentials directly from environment. Those migrations are tracked by #755.

## Secrets

Environment variables are currently used by several compatibility/deployment
paths, but environment configuration is **not** the target durable secret store.

Canonical secret contracts live under `secret_store/` and #755. New durable
configuration must store only permitted secret references/version metadata, not
secret values.

Never commit a populated `.env`, API key, IAM token, OAuth client secret,
short token, signing password, private key, cookie or browser credential.

## Alice Platform desired state

`config/alice/` is a separate YAML desired-state contract:

- `platform.yaml` — services and declared resource requirements;
- `domains.yaml` — desired domain-to-service routing;
- `storage.yaml` — desired persistent storage metadata;
- lane files such as `production.yaml` / `test.yaml` — lane-specific state.

Validate this layer with:

```bash
python -m alice_platform validate
```

The current CLI also provides `python -m alice_platform health <lane>`, which
generates the configured health-check plan. It does not execute provider/DNS/TLS
health probes and is not production acceptance.

Desired state does not prove that Cloud.ru resources exist or match it.
Provider-backed convergence remains separate work under #783.

## Provider credentials

For the current web/provider path, configure provider credentials through the
backend/UI rather than editing `config.py`. See
[provider key rotation](../provider-key-rotation.md).

Legacy encrypted SQL provider credentials remain a compatibility path until
verified Secret Store cutover. Do not add another provider credential store.

## Storage configuration

Storage has two different meanings that must not be confused:

- application durable state: transitional legacy SQL + file-native Memory DB
  migration under #776;
- Alice Platform `config/alice/storage.yaml`: desired infrastructure storage
  metadata.

A YAML `backup_policy` value is configuration intent; it does not prove that a
snapshot scheduler, remote upload, retention job or restore verification exists.

## Android configuration

Android build/signing configuration is separate from Flask process configuration.
See:

- [stable debug signing](android-debug-signing.md);
- [release signing](android-release-signing.md);
- [Android module guide](../../android/README.md).

Signing material belongs in protected CI/local secret storage, never in
repository configuration.

## Verification

Use the narrow check that matches the layer being changed:

```bash
# Python/application configuration and deterministic startup
python scripts/local_launch_smoke.py --offline

# Alice Platform desired-state syntax/contract
python -m alice_platform validate
```

For a provider, OAuth, MCP, DNS/TLS or cloud resource, add the corresponding
integration/live acceptance. Passing either local command above is not evidence
that the external system works.
