# Storage Configuration

Alice Platform manages persistent storage for services that require it. This document explains storage configuration and backup policies.

## Storage Concepts

**Persistent storage** is data that survives service restarts and redeployments. Alice Platform distinguishes between:

- **Ephemeral**: Container-local temporary data (lost on restart)
- **Persistent**: Named volumes shared across container instances
- **Object storage**: S3-compatible buckets for large-scale data

## Chrome Worker Storage

The Chrome worker requires persistent storage for session state, cache, and browser profiles.

### Configuration

Storage is defined in `config/alice/storage.yaml`:

```yaml
storage:
  chrome-state:
    mount_path: /chrome-state
    size: 10Gi
    backup_policy: daily
    retention: 30
```

### Backup Policy

Storage can be backed up automatically based on policy:

- **daily**: Snapshot created daily at 2 AM UTC
- **weekly**: Snapshot created weekly (Sundays at 2 AM UTC)
- **manual**: No automatic snapshots; requires explicit request

### Retention

Snapshots are kept for the specified number of days:

- Test lane: 7 days (minimal retention)
- Production lane: 30 days (standard retention)
- Archives: Moved to cold storage after 90 days

## Multi-Service Storage

If multiple services share storage, the platform ensures:

1. No data corruption from concurrent access
2. Proper permission isolation
3. Consistent backup snapshots across all writers

Example configuration with shared cache:

```yaml
storage:
  chrome-state:
    mount_path: /chrome-state
    size: 10Gi
  shared-cache:
    mount_path: /var/cache/alice
    size: 5Gi
    backup_policy: weekly
```

## Validation Rules

Storage configuration is validated for:

1. **Unique paths**: No two storage mounts on same path
2. **Size format**: Valid Kubernetes size notation (Gi, Mi, etc)
3. **Policy validity**: Only named policies (daily, weekly, manual)
4. **Retention range**: Positive integers only
5. **Service access**: Referenced services exist and can mount storage

## Recovery

In case of data loss, recover storage from the most recent snapshot:

```bash
python -m alice_platform recovery list-snapshots --storage chrome-state
python -m alice_platform recovery restore --storage chrome-state --snapshot 2024-10-01
```

See `docs/platform/recovery.md` for detailed recovery procedures.

## Cost Optimization

Storage costs scale with:
- Total size allocated
- Snapshot retention period
- Number of daily snapshots

Recommendations:
- **Dev/test**: Minimal size (1-5 Gi), short retention (7 days)
- **Production**: Right-size to actual needs, keep 30-day retention
- **Archives**: Move snapshots >90 days to cold storage

## Future Work

- S3-compatible object storage for large datasets
- Cross-lane backup replication
- Automated cleanup of orphaned snapshots
- Encryption at rest configuration
