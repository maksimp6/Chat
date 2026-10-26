# Architecture integration coordination

Use the current `develop` and the exact work head. Delivery and test execution
follow the owner's instruction and [AGENTS.md](../../AGENTS.md).
The [integration ledger](../integration/current-scope.md) links current contracts;
it does not recreate a historical candidate queue.

## Shared boundaries

- `EnvironmentManager` owns environment lifecycle; `RuntimeLoader` loads the
  constrained revision entry point; `RuntimeDispatcher` owns scoped resources.
- Revision loading must not mutate process-global imports, environment variables
  or preview paths. In-process threads are execution units, not hostile-code sandboxes.
- Host-managed preview traffic must not introduce a second Flask/container
  transport or process-global preview configuration.
- Tool and MCP calls converge on `UniversalToolExecutor` and preserve
  `InvocationContext`, `ExecutionTrace` and runtime correlation.
- Storage uses the [native Kristall contract](../database.md). Historical data
  activation and restore remain separate, evidenced operations.
- Plugins, storage providers, conversation agents and coding workers consume
  these contracts rather than creating parallel execution or trace systems.

## Coordination

Assign one primary owner to each deliverable and record direct dependencies.
When changes overlap, retain the implementation that consumes the accepted
shared boundary and document the disposition of the alternative.

Before publication, read the current diff and reconcile the actual base and head.
Tests run only when separately requested and only inside the GitHub Actions
runner. Record `NOT_RUN` otherwise; no removed full-suite, formatter or CI
aggregate is an automatic admission requirement.

Keep the exact SHA, review findings, data compatibility and evidence with the
owning Issue/PR. A merge or successful process check does not establish device
delivery or user acceptance. Promotion to `master` needs separate owner permission.

The old September baseline, fixed PR ordering and Android release-hardening lane
are removed from the active coordination instructions.
