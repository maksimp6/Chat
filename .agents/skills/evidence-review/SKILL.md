---
name: evidence-review
description: Check whether a stated claim or change is backed by code, config or evidence. Use when documentation may be stale, when a privacy or compliance statement needs reconciling with the implementation, when a durability, backup, restore or migration claim is made about storage, when a change needs a security review of trust boundaries, secrets and permissions, or when a hardware design must stay bound to its archived verification.
---
## Purpose
Turn a claim or a change into inspectable evidence, and separate what is shipped from what is planned. Five modes share one rule: an absent proof stays unknown, and a plan is never reported as an implementation.
- **Docs**: keep authoritative documentation aligned with the shipped behavior.
- **Compliance**: reconcile privacy and data declarations with the implementation (H10).
- **Durable storage**: prove durable writes, crash recovery, backup and migration against the accepted Memory DB contract (H12).
- **Security**: review a change for secrets, permissions, trust-boundary and runtime-isolation regressions.
- **Hardware**: keep an editable circuit and its archived verification bound to one versioned artifact (H13).

## Non-goals
- No legal conclusion and no government filing. Legal interpretation goes to a qualified human.
- Do not change product behavior just to match stale prose.
- Do not turn a roadmap item into a shipped claim.
- No live restore, cutover, destructive migration or live credential use.
- No new SQL compatibility layer, and no treating process-local tables as durable storage.
- Do not assume that a named policy file proves a control exists.
- Do not invent a vulnerability. Report only findings with a concrete failure mode and location.
- Do not claim that ERC or a simulation proves electrical performance, fit or heat behavior.

## Inputs
- The claim or change, and the implementation or config revision it is checked against.
- Documentation to check and the authoritative docs for the area.
- For compliance: the declared data map and dated normative references supplied for review.
- For storage: the accepted storage contract, the exact engine revision, the authoritative consumer inventory, and disposable fixtures.
- For security: the diff or the targeted files, and the repository's security and approval policy.
- For hardware: the native schematic or project, the KiCad version and libraries, the netlist, ERC output, exports and the source manifest.

## Tools
- Repository reads and search, and documentation edits.
- Code, config and workflow inspection. Safe validators already in the repository.
- Reconciliation, fault-injection, restore and ERC or mutation runs only when the owner asks. Mutations happen in disposable copies only.

## Procedure
**Docs mode**
1. Identify the authoritative implementation and what actually ships.
2. Compare the docs for contradictions, omissions and stale claims.
3. Separate shipped behavior from roadmap and future work.
4. Update the smallest authoritative document and its cross-links. Do not copy the same policy into many files.

**Compliance mode**
1. Define the chain: inspector → machine-readable actual state → declared intent → reconciliation.
2. Track data categories, purpose, storage, processors, regions, retention and controls, without collecting personal records into the evidence.
3. Verify actual retention, deletion, correction, blocking, access and backup behavior. Absent evidence stays unknown.
4. Describe negative fixtures (mismatched implementation and documentation) and a synthetic incident drill. Add or run them only when the owner asks.
5. Bind every finding to a code or config revision and the date of its normative source.
6. Separate a deterministic mismatch, which can justify a proposed release gate, from an ambiguous legal question that needs a qualified human decision.

**Durable-storage mode**
1. Identify the authority for each consumer. Distinguish FileMemoryDB, the process-local MemoryDatabase, and any proposed engine.
2. Trace one typed table: acknowledgement follows the required durable barrier, and a fresh-process reopen returns the committed state.
3. Review handling of torn writes, corruption, ENOSPC and concurrency. A failed mutation must never appear committed.
4. Check bounded replay and checkpoint or compaction, and confirm they preserve the last valid state.
5. Plan a restore into an isolated instance, comparing counts, typed invariants and deterministic digests.
6. Only after engine proof, plan migration waves: import or catch-up with the old authority, shadow verification, atomic cutover, and explicit recovery.
7. If encryption is in scope, review what happens when the key is unavailable or wrong, and whether retained backups stay decryptable, with no plaintext fallback. Successful encryption alone is not restore acceptance.

**Security mode**
1. Inspect the changed trust boundaries and permission scopes.
2. Check secret handling in code, logs, traces and error surfaces.
3. Check tool, approval and authentication boundaries.
4. Check workflow and event trust, and whether third-party actions are pinned.
5. Check runtime and resource isolation for the affected code.
6. Report only findings with a concrete failure mode and location. Mark uncertain hypotheses as unverified, and do not promote them to findings.

**Hardware mode** (only where KiCad projects exist)
1. Bind the editable inputs and the generated evidence with SHA-256. Any edit invalidates archived evidence until it is regenerated.
2. Compare the complete pin membership of every net, not generated net labels or codes. Check the component inventory, values, symbols and packages, polarity, power, reset and bypass, timing and load paths.
3. Derive the expected behavior from the values in the actual export.
4. Export and run ERC with isolated config and explicit symbol libraries, when asked. Use all severities.
5. Show that the validator rejects representative defects in disposable copies, when asked.
6. Keep native files, PDF or SVG, netlist, ERC output, manifest, check results and limitations together.

## Approval boundaries
Documentation edits follow the normal workflow, and no production action is implied by prose. Production restore, destructive migration, key administration, authority cutover, live credential use and any legally significant submission each need their own explicit authorization. Simulation and physical testing need their own valid inputs and task scope. Do not alter operator configuration or hardware.

## Validation
- Docs: each concrete claim is supported by the current implementation or explicitly labeled planned or unverified.
- Compliance: known mismatches are detected by code reading or owner-requested fixtures, unknowns stay visible, and the report contains evidence rather than raw personal data.
- Storage: required fault cases and fresh-process restore are shown by owner-requested runs, or reported as `NOT_RUN`. Each migrated consumer has explicit authority evidence. No mixed-authority transition is called complete.
- Security: each finding is reproducible or directly evidenced, and its severity rationale is stated without sensational labels.
- Hardware: source and evidence hashes match, topology and value assertions hold, and errors, warnings and exclusions are reported separately. Checks that were not run are `NOT_RUN`.

## Failure behavior
- If implementation state is unclear, document the uncertainty instead of guessing.
- Keep draft engines and legacy compatibility distinct. A missing durable barrier, a skipped restore or unverified platform persistence blocks the claim it supports.
- Do not claim that an inspector, a release gate or a shipped capability exists merely because this skill describes one.
- If KiCad or another required tool is unavailable, run the archived checks and label the live checks as skipped. Never swap in a substitute model silently.

## Output
- Docs: changed documents, corrected claims and open gaps.
- Compliance: evidence map, deterministic discrepancies, unknown controls, normative freshness and human-review questions.
- Storage: an engine, backup and migration evidence matrix, the current authority map, failure cases and the next dependency wave.
- Security: findings with evidence, impact and minimal remediation, and unverified hypotheses listed separately.
- Hardware: a bound manifest, the validation report, the toolchain identity, the live and archived check split, and the physical and simulation limitations.
Sources: [docs/lessons/README.md](../../../docs/lessons/README.md), [docs/memory/memory-db-v1-contract.md](../../../docs/memory/memory-db-v1-contract.md).
