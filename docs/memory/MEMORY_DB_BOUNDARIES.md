# Memory DB — ownership and dependency boundaries

| Component        | Role                                                                        | Current status                |
| ---------------- | --------------------------------------------------------------------------- | ----------------------------- |
| `memory_engine/` | Foundational storage engine: RAM staging, journal, commit, recovery, backup | Standalone contract tested    |
| `agent_memory/`  | Logos application layer and existing runtime persistence                    | Currently uses `Kristall`     |

**Allowed dependency:** `agent_memory → memory_engine` (through the public API). **Forbidden dependency:** `memory_engine → agent_memory` or Logos-specific modules. The engine must remain independently testable.

`Kristall` is the application journal, with checkpoints, `items` and `delete`. `MemoryStore` is the private-file engine `project_board` uses. They write the same frame format but have different APIs and must not open the same file. The running Logos application stays on `Kristall` until a separate, tested adapter and data migration are accepted. This document does not switch runtime, migrate data or authorize merge.
