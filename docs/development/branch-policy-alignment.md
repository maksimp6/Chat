# Branch policy and native enforcement

Canonical admission owner: [#496](https://github.com/maksimp6/Chat/issues/496).
Integration routing belongs to #1072/#1084. This slice reconciles instructions
and prepares settings; it does not merge, deploy, or claim settings were applied.

## Verified gap — 2026-10-12

The report `Alice_Pro_Review_50000_words.md` identifies the native enforcement gap.
Both rulesets were reread through GitHub REST before preparing this change.

| Control | Live master #23532452 | Live develop #24841115 | Proposed change |
| --- | --- | --- | --- |
| Enforcement | Active | Disabled | Activate develop |
| Required checks | Application tests, PostgreSQL integration, Android debug APK | CI required, unenforced | Keep existing contexts; add aggregate, code rules, format, security, CodeQL, local smoke and readiness to master |
| Resolved review threads | Not required | Required, unenforced | Require on both active rulesets |
| Strict base freshness | Enabled | Disabled | Preserve; promotion must be current with master |
| Deletion / force push | Blocked | Configured but unenforced | Preserve; activate develop |
| Bypass actors | None | None | Preserve |
| Required approving reviews | 0 | 0 | Preserve; independent acceptance remains an evidence requirement under #496, not a new paid/model gate |

Feature branches start from develop and target develop. Stacked changes record
their parent. Promotion is a separate develop-to-master PR. A direct master
hotfix needs an explicit task exception. Agents do not push to integration
branches or merge without authorization, even while develop protection is disabled.

## Reviewable settings payloads

- [Master update](rulesets/23532452.json)
- [Develop update](rulesets/24841115.json)

These are proposed complete PUT bodies for the existing rulesets, not snapshots
or an automatically executed workflow. They preserve linear history, code scanning,
existing platform checks, merge methods, branch selection and all other current
rule parameters. Do not replace existing platform checks with the aggregate.
The aggregate fails closed when a selected platform job fails or is missing;
unselected platform jobs may legitimately be skipped.

The GitHub connector can read rulesets but cannot administer them. Applying these
payloads requires an authorized administration capability; do not substitute a
file commit for an actual settings update.

## Apply and verify

1. Re-read both live rulesets and compare with the matrix. If another owner changed
   settings, reconcile the proposal rather than overwriting the concurrent change.
2. Verify all proposed contexts are actually produced on the current promotion
   head, including `Offline startup and restart smoke` and non-draft `Merge readiness snapshot`.
   Snapshot must not require itself in its input list. No merge-queue support is claimed.
3. Apply the reviewed payloads to the existing IDs through authenticated GitHub
   administration; do not introduce bypass actors or disable existing protections.
4. Read back both rulesets. Confirm active enforcement, exact contexts and producer
   bindings, strict master freshness and thread resolution. Check for other applicable
   repository/organization rulesets before claiming the effective policy is verified.
5. Use controlled PR fixtures to prove missing/failing aggregate, failed security,
   unresolved threads and stale promotion are blocked. Verify an admissible PR is
   eligible without merging it; actual merge needs separate owner authorization.
6. Record exact head/base, API evidence and fixture results under #496/#1084.
   Keep native enforcement BLOCKED until those live checks succeed.

GitHub Actions producer binding (integration 15368) does not make PR-editable
workflows tamper-proof. #1117 owns the separate trusted Memory admission groundwork;
its status must not become required before a protected producer is deployed and
verified. This settings proposal does not claim to close that security boundary.

## Handoff

Owner: current Codex session, Process Governor / deterministic shell and GitHub tools.
Contract: current owner request plus #496 and #1084 integration contract.
Write stage: isolated `issue-496-branch-policy-alignment` from current develop.
Scope: routing instructions, enforcement matrix, reviewed payloads and contract checks.
Stop conditions: missing administration capability, live settings drift, unproduced
required context, or absent owner merge authorization. No RDC/device/storage changes.
