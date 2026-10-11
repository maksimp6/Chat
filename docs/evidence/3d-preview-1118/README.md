# Issue #1118 continuation evidence

Canonical implementation: Draft PR #1124, `feature/3d-render-comments-1118`, based on develop.
Original inspected head: `0c8882a16da4d7e0b444583721d62494798be15f`.
Master inspected: `51aaf8273f4f465fe78aa0a79e76a22d8f141e35`.
Scope authorized by owner: implement/review preview contract, no merge or printer operations.
Backend/Infra/Test work: Codex via local checkout and GitHub connector; one implementation lane.
Working branch: `fix/3d-preview-contract-1118`; deliver corrections to existing PR rather than duplicate the objective.
Stop conditions: changed upstream head, failed formatting/security regressions, missing publication capability.

## Real catalog verification

Source: PR #1114 at `d819c20ac3ea4557f5bd0b246e31016186fdfbd2`.
`catalog-check.json` records exact source/PNG hashes, geometry bounds and triangle counts.
All **14 binary STL + 1 unsliced 3MF** rendered successfully; two independent credential-free
worker processes returned byte-identical PNGs for each model. No source files copied or changed.
Original catalog tests on detached source checkout: **9 passed**.
STEP, SCAD and generator code were never executed; no printer connection.

## Security/regressions

Focused renderer/publisher suite: **59 passed** (Pillow 12.3.0 / pytest 9.1.1, Python 3.12).
Additional coverage: incomplete ASCII facet grammar, UTF-16 entity declarations,
compression ratio bombs, unexpected vertex nodes, catalog path canonicalization,
worker environment/timeout, API redirect refusal, head changes during rendering,
orphan image branch creation, identical-tree no-op and output path traversal.
Existing 39 cases remain in the suite. Resource limits: 20 s CPU, 30 s wall,
512 MiB address space, no core dumps/file writes. Child environment contains no token.
This is bounded data processing, **not** a general OS security sandbox.

Trusted publisher preserves the latest scheduled scan (15-minute cron) with default-branch checkout, no persisted
checkout credentials, no PR checkout, no PR artifacts, blob SHA verification and same-repo gate.
Workflow token has contents/write, issues/write and pull-requests/read in publisher job only.
Preview branch is created as an orphan if absent; existing history is never rewritten.
Image-only writes do not match model path triggers. Comments are bot-owned and upserted.
Up to 3 changed models: larger catalog PRs fail explicitly, rather than silently publish a partial set.
The 15-model source check is an offline compatibility check, not a privileged automatic run.

## Activation boundary

Default-branch workflow is not installed by this change. Automatic publication remains inactive
until authorized merge/deployment; no merge is performed. A manually published demonstration
must be labelled as such and cannot count as proof of the automatic trigger.
GitHub exact-head CI is separate evidence; skipped checks are not failures.

## Concurrent upstream fixes retained

Before publication, the remote advanced to `a38e7d763f2d338b20a2e5908de51d8fc0392bff`.
Its commits replaced privileged events with a 15-minute trusted schedule and moved renderer
checks to `scripts/validation/render_preview_contract.py`. These fixes are retained, including
all four additional scan tests. No duplicate PR or privileged-trigger regression is published.
The original workflow_run and contract pull_request_target both trigger the repository's
Zizmor dangerous-triggers gate, so the published design uses the existing safer scheduled scan.
This is an explicit trigger deviation from the original Issue #1118 contract, not an instant PR event.
Pillow remains in the dedicated render test workflow; the main dependency set is unchanged.
No audit rule is suppressed. Local Zizmor 1.30.1 checks are recorded separately from exact-head CI.

## Exact-head CI follow-up

Published `a1e87808f11cd59d7cee48f31562b267357ebe2f` passed the dedicated render contract,
Zizmor, Trivy, dependency review, Format and startup smoke. Application tests/Code rules
reported C901 (72 vs 69) and PLR0911 (20 vs 19). Extract facet decoding, preview blob storage
and final current-head publication into focused helpers; preserve the repository ratchet.
Focused renderer suite plus reliability gate: **60 passed locally** (59 + 1), Ruff passed.
No baseline or rule suppression change. Subsequent exact-head CI is required separately.
