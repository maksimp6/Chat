# Issue #496 — trusted Memory DB admission and safe ruleset rollout

Status: **Draft implementation only; not enforced by GitHub-native rulesets**.

## Scope / source of truth

Canonical issue: [#496](https://github.com/maksimp6/Chat/issues/496).
The accepted Memory DB test baselines come from Draft [#1106](https://github.com/maksimp6/Chat/pull/1106), exact head `a82916624704cf65455d15d73df12faa98509db3`, and its legacy/core parent PRs. This slice adds *new* verifier/workflows only; it does not edit accepted test sources, FileMemoryDB, or working storage.

The protected-base workflow `.github/workflows/trusted-memory-admission.yml` uses `pull_request_target` and checks out only `github.event.pull_request.base.sha`. The trusted Python code fetches candidate file bytes through GitHub's read-only Contents API at **the PR's exact head SHA**; it never checks out or executes candidate files. It posts a commit status named `Alice / Trusted Memory Contracts` to that precise head, using `statuses:write` only, with pending → success/failure/error transitions. A second PR read rejects stale head/base evidence.

The separate `pull_request` *Selftest* workflow has no write permissions. Its green badge is only deterministic unit-test evidence and **cannot** substitute for a trusted production commit status.

### Staged baseline (fail-closed)

- **Legacy:** verify the accepted `agent_memory/file_memory_db.py`, empty `tests/__init__.py`, and absence of known import hooks.
- **Core introduced:** additionally enforce the four accepted core-only test files. Missing/mutated tests reject the PR.
- **Fast introduced:** enforce all 16 accepted SHA-256 artifacts *and* the approved full CI workflow blob (`e69158566e9dbd4f6c92834e3aaee3c9c760d637`). A partial fast rollout is rejected. This intentionally requires explicit policy revision for any future legitimate CI-workflow edit; do not silently change an accepted baseline.
- **Any stage:** candidate changes to the trusted workflow, policy, script, or its test files relative to protected base are rejected. A trusted-policy update needs a separately reviewed, explicit administrator-controlled bootstrap path.

This staging permits the intended parent/core-first integration sequence without requiring files that do not exist yet in `develop` or `master`.

## Native GitHub rulesets: verified configuration and desired delta

The following live values were checked on 11 Oct 2026 (UTC):

| Ruleset | Existing state | Required context delta |
| --- | --- | --- |
| `master` ID `23532452` | `active`, strict, currently `Application tests`, `PostgreSQL integration`, `Android debug APK` | Keep all three; add `CI required` and verified `Alice / Trusted Memory Contracts` from a fixed authorized producer. |
| `develop integration` ID `24841115` | `disabled`, currently `CI required` | After trusted workflow exists on base and the status is verified, enable without requiring another human's approval; add `Alice / Trusted Memory Contracts`. |

Do **not** apply the ruleset delta until the trusted status has appeared as completed/success on a test PR against **each** target branch and the producer identity is understood. Requiring a check before its trusted workflow can execute risks blocking all merges. In this user-owned, single-contributor repository do **not** set `required_approving_review_count > 0`; record self-review/owner acceptance as separate, correctly labeled evidence.

### Trust caveats that still block final P0 closure

1. The `pull_request_target` workflow file is only trusted when the base branch itself is protected. Until the `develop` ruleset is activated, a direct edit to that branch could compromise the gate.
2. A commit status produced with `GITHUB_TOKEN` appears from the **GitHub Actions** app; different PR-editable workflows may share that producer identity. A genuinely independent immutable-check guarantee should use a dedicated GitHub App with the required status source pinned, or an available organizational required-workflow rule. Do not claim a trusted source merely because the context string matches.
3. GitHub's announced default policy for `pull_request_target` in public repositories is in evaluation in Oct 2026 and scheduled for enforcement on **2 Nov 2026**. The owner must verify repository Actions event-policy allowance before making the check required, or move to a trusted event/provider that remains supported.
4. The current connector does not expose ruleset mutation. No native settings change can be claimed until verified live via REST GET, and negative merge-admission tests are observed.

## Acceptance protocol — no merge in this preparation slice

1. Run unprivileged deterministic selftest and full repository CI on exact Draft head. Preserve existing required checks.
2. On a protected base, validate a real positive PR and ensure its `Alice / Trusted Memory Contracts` status is attached to its **head SHA**, not only the base or merge SHA.
3. Adversarial dry-run: mutate `agent_memory/file_memory_db.py`, delete an accepted core test, add `tests/__init__.py` import side effects, remove `fast-tests` from CI YAML, alter the trusted workflow; each must produce failure.
4. Verify a changed head/base during validation does not produce success for a stale SHA.
5. After one real positive status and verified source, update the **existing** #496 rulesets (do not create duplicate gates), keep all prior required contexts, and test that every failed/skipped/missing status blocks native admission.
6. Confirm no merge, no migration and no deletion of legacy code took place. Document actual ruleset GET snapshots, the exact workflow run IDs, and the new SHA in #496.

The implementation does **not** mark Issue #496 complete or PR #1106 merge-ready on its own.
