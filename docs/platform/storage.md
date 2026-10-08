# Storage Configuration

Alice Platform validates desired-state storage configuration for services that require it. This document describes the configuration contract. Snapshot scheduling, provider-backed backup/restore and recovery commands are not implemented by the current `alice_platform` CLI and must not be treated as available operations.

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

`backup_policy` and `retention_days` are desired-state fields. The current platform code does not yet provision or schedule snapshots from them. Intended policy values are:

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

Provider-backed snapshot restore is planned but is not exposed by the current `alice_platform` CLI. Do not run or document synthetic `recovery list-snapshots` / `recovery restore` commands as operational evidence.

See `docs/platform/recovery.md` for the current recovery boundary and planned procedures.

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
