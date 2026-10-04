# Recovery Procedures

Alice Platform provides automated recovery from common failure scenarios. This document explains how to recover from service failures, data loss, and configuration errors.

## Recovery Types

The platform supports three recovery mechanisms:

1. **Automated restart**: Service restarts on failure (default)
2. **Snapshot restore**: Restore persistent storage from backup
3. **Configuration rollback**: Revert to previous known-good configuration

## Service Restart

When a service fails (crashes, health check fails):

1. The platform detects the failure
2. Logs the failure details (for analysis)
3. Automatically restarts the service
4. Verifies service readiness with health checks
5. If still failing after 3 retries, marks as unhealthy

Configuration:

```yaml
services:
  oauth:
    type: oauth
    restart_policy: always  # always, on_failure, never
    restart_max_retries: 3
    restart_delay: 10s
```

### Health Checks

Each service has health checks that verify it's running:

```bash
python -m alice_platform health production
```

Health checks use:
- **Endpoint**: HTTPS URL from domain config
- **Expected sign_in**: Authentication method (github, passphrase)
- **Timeout**: Maximum response time (30s default)

If health check fails, the service is marked unhealthy and recovery is triggered.

## Storage Recovery

### Listing Snapshots

View available snapshots for a storage volume:

```bash
python -m alice_platform recovery list-snapshots --storage chrome-state --lane production
```

Output:
```
Available snapshots for chrome-state:
  2024-10-04 02:00:00 UTC — automatic daily snapshot
  2024-10-03 02:00:00 UTC — automatic daily snapshot
  2024-10-02 02:00:00 UTC — automatic daily snapshot
```

### Restoring from Snapshot

Restore storage to a specific point in time:

```bash
python -m alice_platform recovery restore \
  --storage chrome-state \
  --snapshot 2024-10-02 \
  --lane production \
  --approve  # Skip confirmation prompt
```

This:
1. Verifies the snapshot exists and is valid
2. Creates a new volume from the snapshot
3. Attaches it to the service
4. Restarts the service with the restored data
5. Verifies service health
6. Keeps old volume for 24 hours before deletion (safe fallback)

### Data Loss Scenarios

**Scenario**: Chrome worker lost all browser state

**Recovery**:
```bash
# Find the most recent snapshot before data loss
python -m alice_platform recovery list-snapshots --storage chrome-state

# Restore to known-good state
python -m alice_platform recovery restore \
  --storage chrome-state \
  --snapshot 2024-10-04 \
  --lane production
```

**Result**: Chrome worker resumes with state from 2024-10-04 02:00 UTC

## Configuration Rollback

When a configuration change causes failures, rollback to the previous version:

```bash
python -m alice_platform recovery rollback-config \
  --to-commit abc1234 \
  --lane production \
  --approve
```

This:
1. Loads the previous configuration
2. Calculates differences from current
3. Shows planned changes (confirmation)
4. Applies the previous configuration
5. Triggers service updates as needed
6. Verifies new state with health checks

### Configuration History

All configuration changes are tracked in git:

```bash
git log config/alice/platform.yaml

# View change details
git show abc1234:config/alice/platform.yaml
```

### Rollback Scenarios

**Scenario**: New configuration causes all services to fail

**Root cause**: Invalid service dependencies introduced cycle

**Recovery**:
```bash
# Check recent changes
git log --oneline config/alice/ | head -5

# Rollback to last known good
python -m alice_platform recovery rollback-config \
  --to-commit abc1234 \
  --approve

# After recovery, investigate the bad change
git diff abc1234 HEAD config/alice/
```

## Failure Diagnosis

### Check Service Status

```bash
python -m alice_platform status production

# Output:
# oauth: RUNNING (healthy, 1 replica)
# chrome: UNHEALTHY (failed health check 3 times)
# agent-shell: RUNNING (healthy, 1 replica)
```

### View Service Logs

```bash
python -m alice_platform logs chrome --lane production --tail 50
```

Output:
```
2024-10-04T10:00:00 - [ERROR] Failed to initialize browser
2024-10-04T10:00:01 - [ERROR] Mounting chrome-state: permission denied
2024-10-04T10:00:02 - [INFO] Restarting service after 1st failure...
```

### Storage Diagnostics

```bash
python -m alice_platform status storage --lane production

# Output:
# chrome-state: 8.5 Gi / 10 Gi (85% full)
#   Last snapshot: 2024-10-04 02:00:00 UTC
#   Mount path: /chrome-state
#   Mounted by: chrome
```

## Emergency Procedures

### Service Is Completely Broken

If a service cannot recover even with restarts:

1. **Verify configuration**: `python -m alice_platform validate`
2. **Check health**: `python -m alice_platform health <lane>`
3. **View logs**: `python -m alice_platform logs <service>`
4. **If recent change**: Rollback config
5. **If data corruption**: Restore storage from snapshot
6. **If still broken**: Escalate to operations team

### Data Center Disaster

If the entire deployment region is lost:

1. Restore from cross-region backups
2. Update DNS to point to recovery region
3. Run health checks to verify
4. Monitor for issues during traffic migration

### Security Incident

If a service is compromised:

1. Isolate the service (remove from load balancer)
2. Rotate all secrets (see `docs/platform/secrets.md`)
3. Review logs for unauthorized access
4. Restore from snapshot before incident time
5. Deploy patched image
6. Resume traffic after verification

## Future Work

- Automated cross-region backup replication
- One-click recovery for common failure scenarios
- Automated incident detection and alerting
- Machine learning-based anomaly detection
