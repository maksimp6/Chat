# Alice Platform Architecture

Alice Platform is a configuration-driven deployment system that uses three layers:

1. **Documentation** (docs/platform/) — explains rules and principles
2. **Configuration** (config/alice/) — contains desired state facts (YAML)
3. **Code** (alice_platform/) — validates config and reconciles reality to desired state

This document explains the platform structure and principles.

## Core Services

The platform currently carries legacy control-plane names while the Container Apps runtime migrates to canonical user-facing service names.

Canonical runtime services:
- **alice** — production Alice Pro runtime, 1 vCPU / 1 GiB, min 0 / max 1, 300 s idle timeout.
- **alice-lab** — development/test Alice runtime, 1 vCPU / 1 GiB, min 0 / max 1, 120 s idle timeout.
- **alice-browser** — real Chromium/Playwright/MCP worker, 2 vCPU / 2 GiB, min 0 / max 1, 120 s idle timeout.

The existing **oauth** and **chrome** logical services remain during migration because current authentication/browser consumers still reference them. They must not be deleted or silently renamed until those consumers have migrated and live acceptance is proven. Runtime scaling is fail-closed in config: instance counts are non-negative/positive as appropriate, min cannot exceed max, and idle timeout must be a positive integer.

Current control-plane services:

### OAuth
- **Purpose**: OAuth 2.0 identity provider for Alice
- **Configuration**: `platform.yaml`, `domains.yaml`, `secrets.yaml`
- **Deployment**: `production.yaml`, `test.yaml`
- **Domain**: `oauth.maxxxpavlov.online` (production), test lane equivalent
- **Dependencies**: None
- **Sign-in method**: `github` (production), `passphrase` (test)

### Chrome Worker (legacy logical name during migration)
- **Purpose**: Browser automation and control for Alice Chat
- **Configuration**: `platform.yaml`, `domains.yaml`, `storage.yaml`
- **Persistent storage**: `/chrome-state` volume
- **Domain**: `chrome.maxxxpavlov.online` (production), test lane equivalent
- **Dependencies**: `oauth` (for identity verification)
- **Resources**: Requires more CPU and memory for browser operations

### Agent Shell
- **Purpose**: Runtime execution environment for agent scripts
- **Configuration**: `platform.yaml`, `domains.yaml`
- **Domain**: `agent-shell.maxxxpavlov.online` (production), test lane equivalent
- **Dependencies**: `oauth` (for authentication)
- **Scaling**: Horizontal scaling for parallel executions

## Deployment Lanes

A **lane** is a deployment environment with its own configuration, secrets, and expectations.

### Test Lane
- **Sign-in**: `passphrase` (development only)
- **Secrets**: Test-scoped only (`alice/test/*`)
- **Domain protocol**: HTTPS preferred, HTTP allowed
- **Resources**: Minimal (0.1 CPU, 256MB memory per service)
- **Approval**: No approval required for changes
- **Purpose**: Development, testing, validation of changes

### Production Lane
- **Sign-in**: `github` (required, no passphrase allowed)
- **Secrets**: Production-scoped (`alice/prod/*`)
- **Domain protocol**: HTTPS only
- **Resources**: Full allocation (0.5+ CPU, 512MB+ memory per service)
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
    services: [oauth, chrome, agent-shell]
    scale_overrides:
      chrome: 2
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

### Planning
```bash
python -m alice_platform plan test
python -m alice_platform plan production
```
Compares desired state (config) to observed state (cloud provider) and shows planned changes.

### Health Checks
```bash
python -m alice_platform health test
python -m alice_platform health production
```
Lists URLs and expected authentication methods for each service in the lane.

## Future Work

- **Reconciliation**: Apply planned changes to cloud infrastructure
- **Recovery**: Automated recovery procedures when services fail
- **Real cloud.ru provider**: Fetch true observed state from Cloud.ru API
- **CI integration**: Automate validation on every commit
