# Runtime-scoped artifact storage

`StorageProvider` is the provider-neutral contract for upload, download, and list operations. `LocalDirectoryStorage` is the first integration path and is deliberately local: its `local:` locations are private runtime artifacts, not cloud URLs and are never advertised as synchronized.

Runtime code uses `RuntimeStorage`, which delegates every operation to `RuntimeDispatcher`. Provider adapters and credential resolution live on the dispatcher side in `ScopedStorageRegistry`; revision/runtime code supplies only a provider name and object data. Registrations are keyed by `(runtime_id, owner_id, provider)`, and cross-runtime resource identifiers fail with `RuntimeScopeViolation` before an adapter runs.

Cloud adapters should be registered with `requires_credentials = True`. Credentials must come from the registry's credential resolver (backed by the existing credential service), never from runtime payloads. Trace events include runtime, provider, action, status, and safe error type only; object contents, names, provider exceptions, and credentials are excluded.
