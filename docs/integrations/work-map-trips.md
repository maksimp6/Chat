# Work Map: trips.db workflow

- **Status**: documented; not implemented in Alice Pro
- **Issue**: #470
- **Privacy level**: location-sensitive data; user-explicit control required

## Overview

The Work Map generator processes user-selected `trips.db` (SQLite) databases to visualize travel patterns and generate route summaries. The current implementation is a **manual prototype** verified through standalone analysis. Alice Pro does not yet integrate this as a shipped capability.

This document describes the verified workflow, its design principles, and the boundaries for a future Alice Pro integration.

## Scope

**Current (manual, prototype):**

- Read-only SQLite snapshot of `trips.db`
- Segment-aware distance and duration calculation
- Timezone-aware timestamp handling
- Soft-deleted trip filtering
- Synthetic test fixtures only

**Future (if integrated into Alice Pro):**

- File selection and upload through user UI
- Sandboxed processing and temporary file cleanup
- Execution Trace logging (with coordinate redaction)
- Runtime/tool access following provider-neutral storage interfaces

**Out of scope:**

- Automatic file discovery or access to user drives (Google Drive, Yandex Disk, etc.)
- Publishing or sharing generated maps
- Modifying source `trips.db` files
- Storing coordinates, photos, or generated maps in Git or version control

## Data handling principles

### Read-only snapshots

- Open `trips.db` in read-only mode
- Create a snapshot of necessary tables (e.g., `trips`, `segments`, `waypoints`)
- Never mutate the source database
- Do not retain WAL/SHM files in storage

### Soft-deleted trip filtering

- Filter trips marked as deleted in the database schema
- Preserve segment markers and trip boundaries
- Do not merge segments across deleted trips

### Timezone and time boundaries

- Treat timezone as explicit input, not derived from coordinates
- Define day boundaries relative to user-specified timezone
- Handle daylight saving time transitions correctly
- Take the gap threshold that splits segments as explicit input; there is no universal default

### Distance and duration calculation

- Calculate distance only within contiguous segments
- Never count jumps between segments as travelled distance
- Preserve segment markers at date changes or long gaps
- Sum segment durations accounting for stops and pauses

### Explicit inputs and limitations

The prototype does not infer these from the data. Each one is an input the user
supplies, or a documented limitation of the output:

- timezone and working hours;
- gap threshold between segments;
- waypoint visit state (visited or planned) — the database alone does not prove a visit;
- photo matching to trips or waypoints — best-effort by time and place, not authoritative.

### Privacy and logging

- Keep coordinates, photo URLs, and generated route JSON out of logs and Execution Trace
- Keep `trips.db` and its `-wal`/`-shm` files, private file identifiers or share links, and credentials out of Git, logs and traces
- Use synthetic fixture data for tests; do not publish or share real trips
- Redact or exclude location-sensitive metadata from any output visible to users or logs
- Use privacy-preserving aggregates (e.g., total distance, day count) for summaries

## Architecture

### Current manual workflow

```text
User exports trips.db
  ↓
Manual snapshot / local tools
  ↓
Process trips.db read-only
  ↓
Filter soft-deleted; calculate segments
  ↓
Generate route summary (JSON/map)
  ↓
User review and decision to share/publish
```

### Future Alice Pro integration (design, not shipped)

When Alice Pro integrates Work Map generation, the workflow should follow:

1. **File selection**: User chooses `trips.db` from their device or cloud storage (via the user artifact storage capability, see #340)
2. **Snapshot creation**: Create isolated, temporary copy in runtime-scoped storage
3. **Processing**: Apply segment logic, filtering, and aggregation (no mutations to source)
4. **Logging**: Record processing metadata in Execution Trace; redact coordinates and photo data
5. **Output**: Return synthetic aggregates and route visualizations; never share or publish automatically
6. **Cleanup**: Delete temporary files; retain only user-approved output

Reading a user-supplied `trips.db` snapshot is a file-format and integration
concern. It is not Alice runtime persistence and does not conflict with the
file-native runtime state in #776.

Ownership of the pieces a future integration touches:

- integration support status: #467 and the generated [integration catalog](catalog.md);
- user artifact and provider access: #340, while it remains active;
- runtime and tool authorization: the current RuntimeDispatcher and UniversalToolExecutor contracts ([runtime dispatcher contract](../agents/runtime-dispatcher-contract.md), [Universal Tool Platform](../architecture/universal_tool_platform.md)), and #326 where exposed through MCP;
- documentation lifecycle: #978.

## Testing

- Use synthetic fixtures: generated `trips.db` files with known segments and waypoints
- Test segment calculation with various timezone and gap threshold inputs
- Verify soft-deleted trip filtering
- Do not commit real `trips.db` files, coordinates, photos, or generated maps
- Test coordinate redaction in logs and Execution Trace

## References

- This document: #470
- Integration status: #467
- User artifact storage: #340
- Runtime/tool authorization: RuntimeDispatcher and UniversalToolExecutor contracts; #326 for MCP exposure
- Documentation lifecycle: #978

Historical issues #343, #350 and #351 are not current owners of this work.
