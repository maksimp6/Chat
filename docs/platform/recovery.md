# Recovery Procedures

Status: **planned contract, not an implemented operator CLI**.

Alice Platform currently provides configuration validation/planning and health-related foundations, but the snapshot/restart/rollback commands previously shown in this document are not implemented in the current `alice_platform` CLI. Issue #783 owns the truthful observe → plan → approve → apply → verify → recovery convergence proof.

## What is safe to rely on now

- `config/alice/` is the machine-readable desired-state layer.
- Storage policy fields such as `backup_policy` and `retention_days` describe intended state; they do not prove that Cloud.ru snapshots are being scheduled.
- Recovery must remain fail-closed: missing provider evidence is `UNKNOWN`/`BLOCKED`, never a synthetic successful restore.
- Git history can be used to inspect previous desired-state configuration, but changing infrastructure to match an old commit is a mutation and requires the normal approval/apply/verify path.

## Not implemented yet

The following interfaces are **not current commands** and must not be used in runbooks or acceptance evidence until code and tests land:

- `python -m alice_platform recovery list-snapshots ...`;
- `python -m alice_platform recovery restore ...`;
- `python -m alice_platform recovery rollback-config ...`;
- `python -m alice_platform logs ...`;
- `python -m alice_platform status storage ...`;
- automatic snapshot scheduling/retention enforcement merely because those values exist in `storage.yaml`.

## Required recovery implementation

A future recovery slice must prove, with provider-backed evidence:

1. observe the owned resource and available recovery source;
2. produce a deterministic recovery plan;
3. require approval for destructive/replacement operations;
4. apply only bounded mutations;
5. verify exact resource/image/config identity and service health;
6. preserve a rollback path;
7. record sanitized evidence without secret values;
8. fail closed when ownership, snapshot integrity or provider state cannot be proven.

Storage recovery must use the canonical durable-state boundary owned by #776 rather than inventing a second application database. Platform convergence and recovery are owned by #783.

## Configuration rollback

Git remains the source of history for desired-state files:

```bash
git log -- config/alice/
git show <commit>:config/alice/platform.yaml
```

These commands only inspect history. They do **not** apply or roll back Cloud.ru resources.

## Emergency rule

Until provider-backed recovery is implemented and accepted, use the resource-specific verified runbook for the affected service. Do not infer that a snapshot exists from `backup_policy: daily`, and do not claim recovery success without live verification.
