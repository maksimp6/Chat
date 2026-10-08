# Alice Platform deployment boundary

Status: **planned provider-backed contract; not an implemented deployment CLI**.

Alice Platform currently validates desired-state configuration and can generate a
configured health-check plan. It does not currently expose provider-backed
`plan`, `deploy`, `status`, `logs`, or `recovery` commands.

Issue #783 owns convergence from validated desired state to real Cloud.ru
observation and bounded mutation.

## Commands that exist

From the repository root:

```bash
python -m alice_platform validate
python -m alice_platform health test
python -m alice_platform health production
```

`validate` checks the configuration contract. `health` generates the configured
service endpoints/authentication expectations. Neither command proves that a
Cloud.ru resource exists or that a deployment is healthy.

## Commands that do not exist

Do not use the following historical examples as operational instructions or
acceptance evidence:

```text
python -m alice_platform plan ...
python -m alice_platform deploy ...
python -m alice_platform status ...
python -m alice_platform logs ...
python -m alice_platform recovery ...
```

They remain design vocabulary only until implementation and deterministic tests
land.

## Target deployment lifecycle

A future provider-backed platform deployment must prove this sequence:

1. **Observe** owned Cloud.ru resources with sanitized evidence.
2. **Plan** deterministic drift from `config/alice/`.
3. **Approve** every consequential production mutation.
4. **Apply** only the bounded approved changes.
5. **Verify** exact resource identity, revision/image/config and health.
6. **Recover/rollback** when verification fails.
7. **Record evidence** without secret values.
8. Fail closed as `UNKNOWN` / `BLOCKED` when ownership or provider state
   cannot be proven.

A configuration diff is not an infrastructure plan until it includes observed
provider state.

## Current deployment-specific runbooks

Use the implementation-specific documentation for paths that actually exist:

- [Cloud.ru Container Apps baseline](../cloudru-container-apps.md) — shipped
  client/workflow contract with deterministic tests; live Cloud.ru validation is
  still explicitly pending.
- [VPS production deployment](../production-deployment.md) — legacy/current
  SSH/VPS workflow while deployment ownership/cutover remains open under #869.
- [Host-managed preview validation](../preview-deployments.md) — environment
  gateway validation path with its own enablement/dependency boundaries.

Do not infer that one of these paths is the universal Alice Platform reconciler.

## Approval and safety

Production mutations require explicit owner approval. Secret values must resolve
through their approved runtime/deployment boundary and must not appear in plans,
logs, PRs or ExecutionTrace.

For rollback governance, see [Auditable Rollback Workflow](../rollback_workflow.md).
For the planned platform recovery contract, see [Recovery Procedures](recovery.md).

## Acceptance for #783

The platform deployment layer becomes current only when tests and provider-backed
evidence demonstrate:

- sanitized observed state;
- deterministic drift;
- approval enforcement;
- bounded idempotent apply;
- exact post-apply verification;
- rollback/recovery behavior;
- cost/billing evidence where provisioning can create spend.

Until then, this page intentionally contains no synthetic deploy command.
