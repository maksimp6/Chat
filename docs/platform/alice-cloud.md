# Alice Cloud MVP

Alice Cloud is Alice Pro's own infrastructure control plane. It does not call a third-party cloud API.

The first runtime is Docker on Alice-owned nodes. The control plane manages only containers carrying the `alice.cloud.managed=1` label and refuses destructive operations against anything else.

## Scope

- desired-state reconciliation for create/update/delete;
- scale `0` and `1`;
- start, stop and restart;
- container inventory;
- logs and one-shot runtime metrics;
- provider-neutral registration as `alice`;
- no external provider credentials.

## Safety model

Production actions still honor the existing approval gate. Alice Cloud never uses `shell=True`, validates lane/service names, and checks ownership labels before lifecycle or deletion operations.

The MVP deliberately does not implement multi-node scheduling, overlay networking, volumes, secret distribution, backups or autoscaling. Those belong in later slices after the single-node contract is proven.

## Configuration

Alice Cloud is the default infrastructure provider after registration. An explicit selection can be passed as:

```yaml
cloud:
  provider: alice
```

A service needs an immutable or otherwise controlled image reference:

```yaml
services:
  alice-dev:
    image: ghcr.io/maksimp6/chat/alice@sha256:<digest>
    scale: 0
```

This PR establishes the control plane contract. Deployment to a real node is a separate acceptance step.
