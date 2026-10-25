# Memory DB — review decisions

## Public API and internal tests

Production clients use only `MemoryStore` (`set`, `get`, `commit`, `transaction`, `backup`, `restore`, `close`). Tests of observable contracts should also use this API. An isolated white-box test may inspect `_engine._state` only to assert that a small commit does not deepcopy the entire committed RAM mapping. This is an implementation/performance invariant, not a public API guarantee.

## Backup publication

`backup()` uses exclusive `open("xb")`: an existing destination cannot be overwritten. The destination file is visible during copying and therefore may be incomplete until `backup()` returns successfully. On a caught copy/write/sync error, the implementation attempts to remove the partial destination. A process crash may leave an incomplete artifact; callers must not treat file existence as proof of a valid backup. `restore()` verifies journal integrity before accepting a backup. This is **not** an atomic publish protocol.

## Lint scope

Do not rewrite unrelated code to satisfy additional, non-required lint rules. Fix new issues and obey the repository's required checks. Existing lint debt is a separate task unless it affects correctness or security.

## Acceptance

Keep the legacy journal replay regression, public RAM/commit tests, and backup/restore failure tests. Local green is necessary but does not replace required CI on the final pushed commit. Merge remains blocked until CI and user acceptance.
