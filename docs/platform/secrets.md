# Secrets Configuration

Alice Platform uses external secret storage to keep sensitive credentials out of configuration files. This document explains how secrets are configured and referenced.

## Secret Concepts

A **secret** is sensitive data (API keys, passwords, signing keys) that:

- Never appears in config files or code
- Lives in secure external storage (e.g., HashiCorp Vault, AWS Secrets Manager)
- Is referenced by a canonical path (e.g., `alice/prod/oauth-client-secret`)
- Is injected at runtime by the deployment system

## Secret References

Configuration files reference secrets by path, never by value:

```yaml
# config/alice/secrets.yaml
secrets:
  oauth:
    client_secret: alice/prod/oauth-client-secret
    signing_key: alice/prod/oauth-signing-key
```

The actual secret values are:
1. Created and stored in external secret storage
2. Fetched by the deployment system at runtime
3. Injected as environment variables or mounted files into containers
4. Never logged or exposed in config

## Path Structure

Secret paths follow a pattern: `alice/<lane>/<service>/<secret-name>`

Examples:
- `alice/prod/oauth-client-secret` — OAuth client secret in production
- `alice/test/oauth-client-secret` — OAuth client secret for testing
- `alice/prod/chrome-signing-key` — Chrome worker signing key in production

### Lane Scoping

Secrets are scoped to lanes to prevent accidental cross-lane mixing:

- `alice/prod/*` — Production secrets, high-security requirements
- `alice/test/*` — Test secrets, development-only values

The platform enforces: **Test lane cannot reference production secrets** (`alice/prod/*`).

This prevents:
- Accidentally using production credentials in tests
- Leaking production secrets to developers with test-only access
- Mixing test data with production systems

## Production Secret Requirements

Production secrets must meet these requirements:

1. **Strong randomness**: Generated with cryptographically secure random
2. **Minimum length**: 32+ characters for symmetric keys, 2048+ bits for RSA
3. **Regular rotation**: Changed on a schedule (quarterly minimum)
4. **Access logging**: All fetches audited and logged
5. **Encryption**: Stored encrypted at rest
6. **Network security**: Fetched over authenticated, encrypted channels

## Test Secret Requirements

Test secrets can be simpler:

1. **Uniqueness**: Different from production
2. **Fixedness**: Deterministic (can be hardcoded test values)
3. **Documentation**: Documented for developers

Example test secret setup:

```bash
# Create test secrets
vault kv put secret/alice/test/oauth-client-secret value="test-client-secret-12345678901234567890"
vault kv put secret/alice/test/oauth-signing-key value="test-key-12345678901234567890123456"
```

## Validation Rules

The configuration validator checks:

1. **Valid paths**: All secret refs match `alice/<lane>/<...>` pattern
2. **Lane consistency**: Test lane doesn't reference `alice/prod/*`
3. **No plaintext secrets**: No actual credential values in YAML
4. **No secret patterns**: Rejects strings that look like secrets (base64, PEM keys, etc) outside the secrets section
5. **Service references**: Only defined services can have secrets

### Plaintext Detection

The validator detects common secret patterns:

- **Base64**: 32+ characters of base64 characters (`[A-Za-z0-9+/=]`)
- **Hexadecimal**: 32+ characters of hex (`[A-Fa-f0-9]`)
- **PEM keys**: Strings containing `-----BEGIN` or `-----END`

If found, the config is rejected with an error message.

## Runtime Injection

At deployment time, the system fetches actual secret values:

```python
# alice_platform/providers/secrets.py (future)
def get_secret(path: str) -> str:
    """Fetch secret value from external storage."""
    # Fetches from vault/secrets manager
    # Returns actual value (never logged)
```

Secrets are passed to containers as:
- Environment variables (for small strings)
- Mounted files (for larger values like keys)
- Secrets in configuration (for structured secrets)

## Audit and Compliance

All secret access is logged with:
- Timestamp
- Service requesting the secret
- Secret path (never the value)
- User/system initiating the request
- Result (success/failure)

Production secret access is also:
- Reviewed for anomalies
- Rate-limited to prevent brute-force
- Subject to additional approval for sensitive operations

## Creating and Rotating Secrets

### Creating a new secret

1. Decide the path: `alice/<lane>/<service>/<name>`
2. Generate a secure random value
3. Store in secret storage with access controls
4. Add reference to `config/alice/secrets.yaml`
5. Update service configuration to read the secret
6. Deploy the change

### Rotating a secret

1. Generate new secret value
2. Create new version in secret storage
3. Update the secret path (optional) or version reference
4. Update config to reference new version
5. Deploy the change
6. Wait for old instances to drain
7. Archive old secret (or delete after retention period)

## Future Work

- Automatic secret rotation based on schedule
- Encryption key versioning and rotation
- Support for multiple secret backends (Vault, AWS Secrets Manager, Azure Key Vault)
- Secret scanning in CI/CD to catch accidental plaintext exposures
