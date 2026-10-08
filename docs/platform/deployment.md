# Deployment Guide

Status: **desired-state and validation contract; not an implemented deployment CLI**.

Alice Platform currently validates `config/alice/` and generates a configured health-check plan. The public CLI does not expose `plan`, `deploy`, `status`, `logs`, `reconcile`, or `recovery` commands. Tests intentionally reject unobserved mutation commands. Production deployment remains owned by the repository's focused deployment workflows/runbooks and the open ownership decision in #869; this document must not invent a parallel deployment path.

## Current supported commands

### Validate desired state

```bash
python -m alice_platform validate
```

This loads the canonical YAML files and enforces the validation implemented in `alice_platform/config.py`: schema/top-level fields, service dependencies, secret-pattern rules, service/domain references, HTTPS in production, and lane invariants.

### Generate the health plan

```bash
python -m alice_platform health production
python -m alice_platform health test
```

This prints configured service endpoints and expected sign-in methods. It does **not** contact the endpoints and is not live deployment evidence.

## Current core desired state

The canonical core services are:

- `oauth`;
- `chrome`.

`agent-shell` is intentionally excluded from the current core platform contract.

## Deployment boundary

The intended lifecycle is:

`observe → plan → approve where required → apply → verify → repair/status`

That full provider-backed lifecycle is not exposed by the current `alice_platform` CLI. #783 owns the convergence proof. Until implementation and tests land:

- do not document synthetic `alice_platform deploy/plan/status/logs/recovery` commands;
- do not infer Cloud.ru mutations from a successful config validation;
- do not infer live health from the generated health plan;
- do not invent approval tokens such as `ALICE_APPROVE`;
- do not claim automatic test/production deployment merely because a lane YAML file changed.

## Change inspection

Git can safely inspect desired-state history:

```bash
git log --oneline -- config/alice/
git show <commit>:config/alice/platform.yaml
git diff <old> <new> -- config/alice/
```

These commands inspect configuration only. They do not apply infrastructure changes.

## Production operations

Use the focused, verified production workflow/runbook that owns the target resource. The SSH-vs-RDC production ownership question is tracked by #869 and must not be resolved implicitly here.

Any production mutation still requires the repository's normal approval, exact-head CI and live post-deploy verification rules.

## Future platform work

- provider-backed observation;
- deterministic drift planning;
- bounded approved reconciliation;
- live health verification;
- provider-backed recovery and rollback evidence;
- full convergence proof for #783.
