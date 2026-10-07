# Domain Configuration

Alice Platform uses `config/alice/domains.yaml` as the desired-state mapping from hostnames to logical services.

## Current canonical domains

Current `master` declares:

- `oauth.maxxxpavlov.online` → `oauth`;
- `chrome.maxxxpavlov.online` → `chrome`.

There is no `agent-shell` domain in the current canonical config.

## Current configuration

```yaml
oauth.maxxxpavlov.online:
  service: oauth
  protocol: https
  redirect_hosts:
    - localhost
    - localhost:3000
    - 127.0.0.1
    - 127.0.0.1:3000

chrome.maxxxpavlov.online:
  service: chrome
  protocol: https
```

The production and test lanes currently reference the same two logical services. Lane membership and authentication expectations come from `production.yaml` and `test.yaml`.

## What validation actually proves

Current config validation proves:

1. a domain that names a service references an existing service;
2. production configuration rejects `protocol: http`;
3. all declared services are present in at least one lane;
4. the rest of the config passes the schema, dependency, secret and lane invariants implemented in `alice_platform/config.py`.

Current validation does **not** prove:

- DNS resolution;
- TLS certificate validity;
- endpoint reachability;
- that every OAuth `redirect_hosts` value is reachable;
- that an OAuth server actually enforces those redirect hosts;
- live Cloud.ru routing.

Those require separate provider/live acceptance evidence.

## Health plan

```bash
python -m alice_platform health production
```

This derives configured endpoints and expected sign-in methods for `oauth` and `chrome`. It does not perform network probes.

## Future work

- provider-backed DNS observation;
- TLS/certificate validation;
- live endpoint probes;
- reconciliation of desired domains against Cloud.ru routing;
- acceptance evidence tied to the exact deployed revision.
