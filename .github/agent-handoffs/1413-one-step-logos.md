# #1413: one visible `logos` step

Owner request: «сделать всего один шаг с названием logos». This is a new manual
entry point reusing the existing native CLIs, not a second Logos implementation.
Canonical case: #1413. Implementation owner for this change: current ChatGPT
session; no separate engineer or model is claimed to be executing it.

Base develop: `081d07af8f854c81201c9341c46c7ed5d3ee9ecf`.
Branch: `fix/1413-one-step-logos`. Deliverable: `.github/workflows/logos-once.yml`
has one job and one step, both named `logos`, without a job-level condition that
silently skips a manual request. Existing RAG work modes remain available.

The step checks out its exact triggering SHA without storing a Git credential,
then `scripts/logos_once.py` validates inputs and calls the existing hard hold
before dependency installation, context collection, knowledge preparation or
provider access. It does not lift D0 or change the repository variable/profile.
Once separately admitted, the wrapper reuses `logos_context`, `logos_knowledge`
and `logos_dialogue`, including their own checks, checkpoints and receipts. It
does not retry a failed stage or replace the requested model. Dependency setup
receives no provider or GitHub credential; context collection receives only the
GitHub read token; knowledge preparation receives only its provider credentials.

Result delivery for this entry point is the Actions step log and summary, not a
new artifact upload. The full native result is UTF-8 JSON, base64-encoded into
ordered `LOGOS_RESULT_PART=i/n:...` lines with a `LOGOS_RESULT_SHA256` digest.
Concatenate the parts after `:`, decode base64, verify SHA-256 and parse JSON.
This preserves long replies and response IDs without treating reply text as
workflow commands. While the existing dialogue process polls its provider,
changed checkpoints are emitted with `LOGOS_CHECKPOINT_PART` and their own
digest, so an interrupted job can retain a known response ID without a new POST.
Summaries display the reply if it fits the summary limit;
otherwise they explicitly point to the complete log chunks. Log retention is
the repository's existing retention, not a durable archive guarantee.

The owner's existing >30-second job reporting policy stays in the same step;
the original execution exit and status remain visible independently of policy
PASS. A workflow run is not proof of a model call or user acceptance.

No merge, provider generation, variable/permission change or device action is
part of implementation/testing. Paid/live native testing remains blocked by
the current hard hold. A direct public Git checkout became available, and the
final change was validated there. The canonical formatter also repaired four
pre-existing formatting-only differences in mcp_routes, printing3d/service,
android_diagnostics and test_cloudru_secret_admin; no behavior change is intended.

Validation: the new entry-point tests and existing job-policy suite pass (101
tests). The broader guard/generation/policy selection initially exposed five
existing launch-hold workflow-expectation failures; the same five were reproduced
on a clean detached worktree at the base SHA. They are not marked PASS and those
tests are not weakened here. The policy wiring test accepts the single-step EXIT
trap, with positive/negative real-shell and exact 30-second boundary controls;
the owner's duration norm is unchanged. Exact final CI/head/pre_push evidence is
reported in the PR. Live execution and owner acceptance remain separate.

## Owner follow-up: remove the obsolete build system (#1423)

Owner request: «удали gradle». Continue in the same PR #1577 rather than create
a duplicate. Remove the four legacy build configuration files, the release
integration, dependency updater and obsolete build commands. Keep the ban test
unchanged and verify real tracked-file and executable-reference negative controls.

The direct SDK APK is only a browser controller, not the retained full
embedded-backend application. Therefore the retired release workflow fails
explicitly before signing credentials/publication; it does not publish the direct
controller as a substitute. Android source and signing-secret values are preserved.
Direct-build shell contracts are checked; a real APK/device acceptance is not
claimed because this checkout has no Android SDK. #1423 remains open for those
acceptance criteria.

Related tests now use current direct-build paths and the actual six-field CI
platform schema; this changes no runtime routing or SQL policy. The one-step
runner and the owner's duration policy remain unchanged. Final focused validation,
exact tree, pre_push and current-head CI are recorded in the PR.
