# Alice Platform Architecture

Alice Platform is a configuration-driven deployment system that uses three layers:

1. **Documentation** (docs/platform/) — explains rules and principles
2. **Configuration** (config/alice/) — contains desired state facts (YAML)
3. **Code** (alice_platform/) — validates config and reconciles reality to desired state

This document explains the platform structure and principles.

## Core Services

The current canonical desired state contains two core services:

### OAuth
- **Purpose**: OAuth 2.0 identity provider for Alice
- **Configuration**: `platform.yaml`, `domains.yaml`, `secrets.yaml`
- **Deployment**: `production.yaml`, `test.yaml`
- **Domain**: `oauth.maxxxpavlov.online` (production), test lane equivalent
- **Dependencies**: None
- **Sign-in method**: `github` (production), `passphrase` (test)

### Chrome Worker
- **Purpose**: Browser automation and control for Alice Chat
- **Configuration**: `platform.yaml`, `domains.yaml`, `storage.yaml`
- **Persistent storage**: `/chrome-state` volume
- **Domain**: `chrome.maxxxpavlov.online` (production), test lane equivalent
- **Dependencies**: `oauth` (for identity verification)
- **Resources**: Requires more CPU and memory for browser operations


## Deployment Lanes

A **lane** is a deployment environment with its own configuration, secrets, and expectations.

### Test Lane
- **Sign-in**: `passphrase` (development only)
- **Secrets**: Test-scoped only (`alice/test/*`)
- **Domain protocol**: HTTPS preferred, HTTP allowed
- **Resources**: Defined by the canonical service config; current OAuth is 0.1 CPU / 256Mi and Chrome is 0.5 CPU / 1024Mi
- **Approval**: No approval required for changes
- **Purpose**: Development, testing, validation of changes

### Production Lane
- **Sign-in**: `github` (required, no passphrase allowed)
- **Secrets**: Production-scoped (`alice/prod/*`)
- **Domain protocol**: HTTPS only
- **Resources**: Defined by the same canonical service config unless a validated lane-specific contract is added
- **Approval**: All changes require explicit approval
- **Purpose**: Live user-facing services

## Configuration Structure

All configuration lives in `config/alice/` and is validated before deployment.

### platform.yaml
Defines services, dependencies, and resource requirements:

```yaml
services:
  oauth:
    type: oauth
    depends_on: []
    scale: 1
    resources:
      cpu: 0.5
      memory: 512Mi
```

### domains.yaml
Maps hostnames to services and protocols:

```yaml
oauth.maxxxpavlov.online:
  service: oauth
  protocol: https
  redirect_hosts:
    - oauth.maxxxpavlov.online
```

### production.yaml / test.yaml
Lane-specific overrides:

```yaml
lanes:
  production:
    sign_in: github
    services: [oauth, chrome]
```

### secrets.yaml
References to secret storage (never values):

```yaml
secrets:
  oauth:
    secret_ref: alice/prod/oauth-client-secret
    signing_key: alice/prod/oauth-signing-key
```

### storage.yaml
Persistent storage configuration:

```yaml
storage:
  chrome-state:
    mount_path: /chrome-state
    size: 10Gi
    backup_policy: daily
```

## Validation Rules

The config system enforces nine validation rules automatically:

1. **Schema**: Unknown fields rejected; required fields enforced
2. **Dependencies**: Cycles detected; nonexistent deps rejected
3. **Secrets**: Only secret refs allowed in `secrets.yaml`; plaintext patterns rejected everywhere
4. **HTTP**: Production lane requires HTTPS only
5. **Service references**: Domains reference existing services; all services deployed in at least one lane
6. **Production invariants**: Must use `sign_in: github`
7. **Test invariants**: Cannot reference production secrets
8. **Lane isolation**: Secrets not shared between lanes
9. **Documentation sync**: Services in code match docs (enforced by test)

## Operations

### Validation
```bash
python -m alice_platform validate [--config-dir config/alice]
```
Checks all YAML files for schema, semantic, and policy violations.

### Health Checks
```bash
python -m alice_platform health test
python -m alice_platform health production
```
Generates the configured health-check plan: service endpoints and expected authentication methods. It does not contact Cloud.ru or prove that the services are live.

## Current implementation boundary

The public `alice_platform` CLI currently exposes only `validate` and `health`. Tests intentionally reject unobserved mutation commands such as `plan` and `reconcile`. The config layer is therefore a validated desired-state contract, not evidence that Cloud.ru resources were observed, created, reconciled, restarted or recovered.

## Future Work

- **Provider-backed observation and planning**: acquire sanitized real Cloud.ru state and derive deterministic drift
- **Reconciliation**: apply only bounded approved changes to cloud infrastructure
- **Recovery**: provider-backed recovery procedures with verification and rollback evidence
- **Convergence proof**: demonstrate the full `observe → plan → approve → apply → verify → repair/status` lifecycle owned by #783
