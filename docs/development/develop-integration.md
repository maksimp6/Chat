# Develop integration and protected promotion

Issues [#1072](https://github.com/maksimp6/Chat/issues/1072) and
[#1084](https://github.com/maksimp6/Chat/issues/1084) own this workflow.
Feature PRs can target `develop`; promotion is a separate PR from `develop` to
protected `master`. Open a promotion PR only when the branches have an actual diff.

## Workflow inventory

| Workflow | PR bases | Push | Merge group | Path filtering |
| --- | --- | --- | --- | --- |
| CI | All, including develop and stack parents | master | None | No event filter; jobs use the platform classifier |
| Format | All; same-repository heads only | None | None | None |
| Security checks | All, including develop and stack parents | master | None | None |
| CodeQL | All, including develop and stack parents | master | None | None |
| Local launch smoke | All | None | None | None |
| Remote Desktop Commander validation | All | None | None | RDC sources and its workflow |
| Merge readiness snapshot | Non-draft PRs into master | None | None | None |
| Host-managed preview validation | All; job requires ALICE_HOST_PREVIEW_ENABLED | All branches | None | None |

Security and CodeQL previously filtered PRs to `master`. Removing that PR filter
lets develop and stacked PRs receive the same scans without changing the jobs,
permissions, schedules or master push triggers. No merge-group checks are provided;
do not enable a merge queue on the assumption that these workflows support it.

`CI required` runs with `always()` after the classifier, code rules and platform
jobs. `scripts/ci_platform_changes.py --verify` requires selected suites to succeed,
rejects failure/cancellation/missing results, and permits a skipped suite only when
the classifier did not select it. Documentation-only changes skip platform suites
but still run code rules and the aggregate; workflow changes select infrastructure
tests. Unknown or shared dependency paths conservatively select broader suites.
The separate Security and CodeQL workflows still run for documentation-only PRs.

Format produces a correction artifact when formatting changes are necessary; its
success alone is not proof of a clean tree. Run `bash scripts/pre_push.sh` in the
real checkout before publication, apply and commit corrections, and validate the
new head. The application suite also runs `bash scripts/format.sh check` when selected.

## Integration and promotion

1. Create an isolated feature branch from the intended current base and open a PR
   into develop. Keep it draft while implementation and checks change.
2. Verify the exact head's `CI required`, formatting result, security and both
   CodeQL languages. Record run links, expected skipped jobs and unresolved findings.
3. Resolve review conversations and follow the maintainer policy in `AGENTS.md`.
   A green aggregate is evidence of selected tests, not permission to bypass reviews.
4. Open the actual develop-to-master diff as a promotion PR. Synchronize with current
   master and require fresh exact-head checks, resolved reviews and readiness evidence.
5. Merge only through GitHub's protected API with the current head SHA after the
   owner's authorization. Never force-push master or weaken protection to unblock it.

## Enforcement and acceptance boundary

The owner's #1084 contract records ruleset `develop integration` (#24841115) as
Disabled: PR required, conversations resolved, no bypass, no deletion/force push,
and `CI required`; strict base freshness is disabled for stacked PRs. This change
does not modify or enable any ruleset. Master protection remains unchanged.
Live settings must be inspected before proposing enforcement changes, and enabling
the develop ruleset requires its separate owner acceptance.

The regression tests in `tests/test_ci_develop_workflows.py` prove workflow
eligibility for master, develop and stack parents and the always-run aggregate.
`tests/test_ci_platform_changes.py` exercises documentation skips, changed dependencies
and failed/cancelled/missing selected suites. These tests do not prove live GitHub
delivery, branch protection or independent review. Record those results on the
implementation PR; keep #1084 open until its live acceptance criteria are met.
