# Deployment Guide

Alice Platform automates deployment of services from configuration. This document explains the deployment process, approval gates, and how to verify deployments.

## Deployment Overview

Deployments follow this process:

1. **Configuration committed** to git (config/alice/*.yaml)
2. **Validation** runs in CI (all rules checked)
3. **Planning** calculates needed changes
4. **Approval** required for production changes
5. **Execution** applies changes to infrastructure
6. **Verification** ensures services are healthy

## CI Validation

Every commit triggers automatic validation:

```bash
# Runs in CI (`.github/workflows/ci.yml`)
python -m alice_platform validate

# Checks:
# - Schema: valid YAML, required fields present
# - Semantics: no cycles, valid references
# - Secrets: no plaintext credentials
# - Policies: production=github, test!=prod_secrets, etc.
```

If validation fails, the PR cannot merge.

## Deployment Planning

To see what changes will be deployed:

```bash
python -m alice_platform plan test
python -m alice_platform plan production
```

Example output:

```
Plan for production:
  1. [create] oauth: Create oauth 🔒 needs approval
  2. [update] chrome: Update chrome scale from 1 to 2 🔒 needs approval
  3. [report_orphan] agent-shell: Orphaned container: agent-shell
```

### Action Types

- **CREATE**: Deploy new service (requires approval in production)
- **UPDATE**: Change service configuration (requires approval in production)
- **DELETE**: Remove service (not done automatically, manual only)
- **REPORT_ORPHAN**: Service exists in reality but not in desired config (investigate)

## Approval Gate

Production changes require explicit approval before execution:

### For Manual Deployments

```bash
python -m alice_platform deploy --lane production
# Prompts for approval before applying changes
```

### For CI/CD Pipeline

Set approval in environment:

```bash
# GitHub Actions workflow
- name: Deploy production
  if: github.ref == 'refs/heads/master'
  env:
    ALICE_APPROVE: ${{ secrets.ALICE_APPROVE }}
  run: python -m alice_platform deploy --lane production
```

The `ALICE_APPROVE` token is issued by:
- Manual approval via GitHub
- Automated approval system (if configured)
- Deployment service (with restricted permissions)

## Test Lane Deployment

Test lane requires no approval:

```bash
python -m alice_platform deploy --lane test
```

Changes are applied immediately after planning.

## Deployment Verification

After deployment, verify services are healthy:

```bash
# Check service status
python -m alice_platform status production

# Run health checks
python -m alice_platform health production

# View recent logs
python -m alice_platform logs oauth --lane production --tail 100
```

Expected output after successful deployment:

```
oauth: RUNNING (healthy, 1 replica)
  Endpoint: https://oauth.maxxxpavlov.online
  Health check: PASSED (response 200)
  Uptime: 2 minutes

chrome: RUNNING (healthy, 2 replicas)
  Endpoint: https://chrome.maxxxpavlov.online
  Health check: PASSED (response 200)
  Uptime: 1 minute

agent-shell: RUNNING (healthy, 1 replica)
  Endpoint: https://agent-shell.maxxxpavlov.online
  Health check: PASSED (response 200)
  Uptime: 2 minutes
```

## Rollback

If deployment causes failures, rollback to previous configuration:

```bash
python -m alice_platform recovery rollback-config --lane production
```

This reverts to the last deployed configuration.

## Change Tracking

All deployments are tracked in git commit history:

```bash
# View deployment history
git log --oneline config/alice/

# See what changed in a deployment
git show abc1234:config/alice/platform.yaml

# Compare two versions
git diff abc1234 def5678 config/alice/
```

## Deployment Checklist

Before deploying to production:

- [ ] All CI checks pass (validation, tests, linting)
- [ ] Configuration is reviewed and approved
- [ ] Health checks defined for all services
- [ ] Secrets are created and accessible
- [ ] Backups are recent and verified
- [ ] Runbook for rollback is available
- [ ] Monitoring and alerts are configured

## Environment-Specific Deployment

### Deploying Only to Test

Make changes in `test.yaml` only:

```bash
git checkout -b add-test-feature
# Edit config/alice/test.yaml
python -m alice_platform validate
python -m alice_platform plan test
git commit -m "Add test feature"
git push
# Merge PR → automatic test deployment
```

### Deploying to Both Lanes

Make changes that affect both lanes:

```bash
# Edit config/alice/platform.yaml (affects both lanes)
# Or edit both production.yaml and test.yaml

python -m alice_platform plan test
python -m alice_platform plan production
# Review plans, ensure consistency
```

## Disaster Recovery Deployment

If deploying from backups:

1. Stop current deployment
2. Restore configuration from git tag
3. Restore storage from snapshots
4. Apply configuration changes
5. Verify all services healthy

```bash
# Restore from known-good commit
git checkout tags/v1.0.0 -- config/alice/

# Restore storage
python -m alice_platform recovery restore --storage chrome-state --snapshot 2024-10-01

# Deploy restored config
python -m alice_platform deploy --lane production --approve
```

## Future Work

- Gradual rollout (canary deployments)
- Automated health check integration
- Blue/green deployments for zero-downtime updates
- Integration with external approval systems (Slack, email)
- Automatic rollback on health check failures
- Metrics-based deployment validation
