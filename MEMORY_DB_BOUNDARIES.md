# Memory DB — ownership and dependency boundaries

| Component | Role | Current status |
|---|---|---|
| `memory_engine/` | Foundational storage engine: RAM staging, journal, commit, recovery, backup | Standalone contract tested |
| `agent_memory/` | Alice application layer and existing runtime persistence | Currently uses `FileMemoryDB` |

**Allowed dependency:** `agent_memory → memory_engine` (through the public API). **Forbidden dependency:** `memory_engine → agent_memory` or Alice-specific modules. The engine must remain independently testable.

The existing `FileMemoryDB` and new `MemoryStore` have different APIs, journal formats and commit semantics. They are not yet interchangeable and must not open the same file. The running Alice application stays on `FileMemoryDB` until a separate, tested adapter and data migration are accepted. This document does not switch runtime, migrate data or authorize merge.
