# Domain Configuration

Alice Platform uses domain routing to direct traffic to the correct service. This document explains how domains work and are configured.

## Domain Concepts

A **domain** is a hostname that maps to a single logical service. Domains must:

- Be unique (no duplicates)
- Reference an existing service
- Specify a protocol (https, http)
- Include redirect hosts for OAuth callback flows (if service is OAuth)

## Production Domains

The platform uses the domain `maxxxpavlov.online` for all production services:

- `oauth.maxxxpavlov.online` — OAuth 2.0 identity provider
- `chrome.maxxxpavlov.online` — Chrome worker for browser operations
- `agent-shell.maxxxpavlov.online` — Agent execution runtime

### HTTPS Requirement

Production domains **must** use HTTPS. HTTP is rejected at config validation time.

### OAuth Redirect Hosts

OAuth services require `redirect_hosts` — hostnames where OAuth clients can complete authentication callbacks.

For production:
```yaml
oauth.maxxxpavlov.online:
  service: oauth
  protocol: https
  redirect_hosts:
    - oauth.maxxxpavlov.online
    - localhost:8000  # Local dev testing
```

The platform validates that:
- At least one redirect host exists
- Each redirect host is reachable and correctly configured on the OAuth service
- Clients only use whitelisted redirect hosts (prevents open redirect attacks)

## Test Domains

Test lane uses the same domain structure as production but with `passphrase` authentication instead of GitHub.

```yaml
oauth.maxxxpavlov.online:  # Same domain in both lanes
  service: oauth
  protocol: https
```

Different lanes can use the same domain hostname because they are isolated at the network and infrastructure level.

## Domain Validation

When validating config, the system checks:

1. **Duplicate domains**: Each hostname appears only once
2. **Service existence**: Referenced service exists in `platform.yaml`
3. **Protocol in production**: Production lane domains use https only
4. **Redirect host format**: OAuth services have valid redirect hosts

## Configuration Format

Domains are defined in `config/alice/domains.yaml`:

```yaml
oauth.maxxxpavlov.online:
  service: oauth
  protocol: https
  redirect_hosts:
    - oauth.maxxxpavlov.online

chrome.maxxxpavlov.online:
  service: chrome
  protocol: https

agent-shell.maxxxpavlov.online:
  service: agent-shell
  protocol: https
```

## Health Checks

The platform generates health check URLs from domain configuration:

```bash
python -m alice_platform health production
```

Output:
```
Health checks for production:
  oauth: https://oauth.maxxxpavlov.online
    Expected sign_in: github
  chrome: https://chrome.maxxxpavlov.online
    Expected sign_in: github
  agent-shell: https://agent-shell.maxxxpavlov.online
    Expected sign_in: github
```

Each health check URL is derived directly from domain config, never hardcoded in code.

## Future Work

- DNS validation: Verify domains actually resolve
- Certificate validation: Check SSL certificates are valid and not expired
- Health probes: Actually test endpoints with Playwright
